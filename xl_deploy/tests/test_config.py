import pytest

from xl_deploy.config import GitHubConfig


def test_config_loads_repository_and_hides_token_from_repr():
    config = GitHubConfig.from_env(
        {
            "GITHUB_REPOSITORY": "owner/project",
            "GITHUB_TOKEN": "top-secret",
        }
    )

    assert config.owner == "owner"
    assert config.repo == "project"
    assert config.token == "top-secret"
    assert "top-secret" not in repr(config)


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"GITHUB_REPOSITORY": "owner/project"},
        {"GITHUB_REPOSITORY": "not-a-repository", "GITHUB_TOKEN": "token"},
    ],
)
def test_config_rejects_missing_or_invalid_credentials(environment):
    with pytest.raises(ValueError):
        GitHubConfig.from_env(environment)


def test_config_rejects_unsafe_timeouts_and_page_limits():
    with pytest.raises(ValueError):
        GitHubConfig(owner="owner", repo="repo", token="token", timeout_seconds=0)
    with pytest.raises(ValueError):
        GitHubConfig(owner="owner", repo="repo", token="token", max_pages=0)
