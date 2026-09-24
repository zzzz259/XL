from dataclasses import dataclass

import pytest

from xl_deploy.config import GitHubConfig
from xl_deploy.github import GitHubClient


@dataclass
class Response:
    status_code: int
    payload: object

    def json(self):
        return self.payload


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, timeout):
        self.calls.append((method, url, headers, timeout))
        return self.responses.pop(0)


def make_client(responses, **config_overrides):
    transport = FakeTransport(responses)
    config = GitHubConfig(owner="owner", repo="repo", token="secret", **config_overrides)
    return GitHubClient(config, transport=transport), transport


def test_latest_sha_uses_injected_transport_and_quotes_branch():
    client, transport = make_client([Response(200, {"commit": {"sha": "abc123"}})])

    assert client.latest_sha("feature/a") == "abc123"
    method, url, headers, timeout = transport.calls[0]
    assert method == "GET"
    assert "/branches/feature%2Fa" in url
    assert headers["Authorization"] == "Bearer secret"
    assert timeout == 10


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        ([{"name": "XL CI", "head_sha": "deadbeef", "head_branch": "debug",
           "status": "in_progress", "conclusion": None}], "pending"),
        ([{"name": "XL CI", "head_sha": "deadbeef", "head_branch": "debug",
           "status": "completed", "conclusion": "failure"}], "failure"),
        ([{"name": "XL CI", "head_sha": "deadbeef", "head_branch": "debug",
           "status": "completed", "conclusion": "success"}], "success"),
        ([{"name": "Other workflow", "head_sha": "deadbeef", "head_branch": "debug",
           "status": "completed", "conclusion": "success"}], "pending"),
        ([{"name": "XL CI", "head_sha": "other-sha", "head_branch": "debug",
           "status": "completed", "conclusion": "success"}], "pending"),
        ([{"name": "XL CI", "head_sha": "deadbeef", "head_branch": "test",
           "status": "completed", "conclusion": "success"}], "pending"),
        ([], "pending"),
    ],
)
def test_ci_result_requires_exact_xl_ci_workflow_for_sha_and_branch(runs, expected):
    client, transport = make_client([Response(200, {"total_count": len(runs), "workflow_runs": runs})])

    assert client.ci_result("debug", "deadbeef") == expected
    assert "/actions/runs?" in transport.calls[0][1]
    assert "head_sha=deadbeef" in transport.calls[0][1]
    assert "branch=debug" in transport.calls[0][1]


def test_ci_result_reads_bounded_pages_before_reporting_success():
    first = Response(200, {"total_count": 2, "workflow_runs": [{
        "name": "Unrelated", "head_sha": "a" * 40, "head_branch": "main",
        "status": "completed", "conclusion": "success", "id": 12,
    }]})
    second = Response(200, {"total_count": 2, "workflow_runs": [{
        "name": "XL CI", "head_sha": "a" * 40, "head_branch": "main",
        "status": "completed", "conclusion": "success", "id": 11,
    }]})
    client, transport = make_client([first, second], max_pages=2)

    assert client.ci_result("main", "a" * 40) == "success"
    assert "page=1" in transport.calls[0][1]
    assert "page=2" in transport.calls[1][1]


def test_ci_result_uses_newest_matching_xl_ci_rerun():
    runs = [
        {"name": "XL CI", "head_sha": "deadbeef", "head_branch": "debug",
         "status": "completed", "conclusion": "success", "id": 12},
        {"name": "XL CI", "head_sha": "deadbeef", "head_branch": "debug",
         "status": "completed", "conclusion": "failure", "id": 13},
    ]
    client, _ = make_client([Response(200, {"total_count": 2, "workflow_runs": runs})])

    assert client.ci_result("debug", "deadbeef") == "failure"


def test_ci_result_does_not_claim_success_when_page_limit_truncates_checks():
    response = Response(200, {"total_count": 2, "workflow_runs": [{
        "name": "Other workflow", "head_sha": "a" * 40, "head_branch": "main",
        "status": "completed", "conclusion": "success", "id": 2,
    }]})
    client, _ = make_client([response], max_pages=1)

    assert client.ci_result("main", "a" * 40) == "pending"


def test_ci_result_reports_observed_failure_even_if_other_pages_are_truncated():
    response = Response(
        200,
        {"total_count": 2, "workflow_runs": [{
            "name": "XL CI", "head_sha": "a" * 40, "head_branch": "main",
            "status": "completed", "conclusion": "failure", "id": 2,
        }]},
    )
    client, _ = make_client([response], max_pages=1)

    assert client.ci_result("main", "a" * 40) == "failure"


@pytest.mark.parametrize("status", [401, 404, 500])
def test_github_api_errors_fail_closed_without_leaking_token(status):
    client, _ = make_client([Response(status, {"message": "secret rejected"})])

    with pytest.raises(RuntimeError) as error:
        client.latest_sha("main")
    assert "secret" not in str(error.value)


def test_transport_failures_fail_closed_without_leaking_exception_text():
    def broken_transport(method, url, headers, timeout):
        raise RuntimeError("request failed using Bearer secret")

    client = GitHubClient(
        GitHubConfig(owner="owner", repo="repo", token="secret"),
        transport=broken_transport,
    )

    with pytest.raises(RuntimeError) as error:
        client.latest_sha("main")
    assert str(error.value) == "GitHub API transport failed"
    assert "secret" not in str(error.value)
