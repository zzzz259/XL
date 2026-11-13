"""Observe feature recipients through real transactions and durable recovery."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from xl_deploy.config import DeploymentInventory
from xl_deploy.planner import plan_deployment
from xl_deploy.tests.test_transaction import (
    DONE_NOTICE,
    NEW_SHA,
    START_NOTICE,
    FakeRouter,
    branch_current,
    make_transaction,
)
from xl_deploy.transaction import RECOVERY_NOTICE

NOTE = "Public feature details"
GATEWAY_CHANGE = ["xl_qqbot/bot_app/gateway.py"]


class RecordingRouter(FakeRouter):
    def __init__(self):
        super().__init__()
        self.requests = []

    def announce(self, scope, text, *, notification_id):
        self.requests.append(("lifecycle", tuple(scope), text, notification_id))
        return super().announce(scope, text, notification_id=notification_id)

    def announce_release_note(self, scope, text, *, notification_id):
        self.requests.append(("feature", tuple(scope), text, notification_id))
        return super().announce_release_note(scope, text, notification_id=notification_id)


def inventory_at(tmp_path, *, branch="main", entries=None):
    # IDs deliberately give no branch hint; even the literal "main" watches test.
    if entries is None:
        entries = [
            ("alpha", "main", True, True),
            ("beta", "test", False, True),
            ("gamma", "main", False, True),
            ("main", "test", True, True),
        ]
    values = {
        "instances": [
            {
                "instance_id": name,
                "watch_ref": ref,
                "managed": managed,
                "enabled": enabled,
                "config_path": f"{name}/config.toml",
                "data_roots": [f"{name}/data"],
                "units": {"bot": f"xl-{name}.service"},
                "listen_ports": {"bot": 8811 + index},
                "groups": [f"group-{name}"],
            }
            for index, (name, ref, managed, enabled) in enumerate(entries)
        ],
        "gateway": {
            "unit": "xl-gateway.service",
            "watch_ref": branch,
            "config_path": "gateway/config.toml",
            "listen_port": 8810,
            "data_roots": ["gateway/data"],
        },
    }
    inventory = DeploymentInventory.from_mapping(values, base_dir=tmp_path / "inventory")
    control_token = "test-control-token-012345678901234567890123"
    tokens = {
        i.instance_id: f"test-instance-token-{i.instance_id}-01234567890123456789"
        for i in inventory.instances if i.enabled
    }
    for instance in inventory.instances:
        if not instance.enabled or not instance.managed:
            continue
        instance.config_path.parent.mkdir(parents=True, exist_ok=True)
        instance.config_path.write_text(
            f'[instance]\nid = "{instance.instance_id}"\n'
            '[watch]\ndata_dir = "data"\noutbox_dir = "data/outbox"\n'
            f'[router.instance_ports]\n"{instance.instance_id}" = {instance.listen_ports["bot"]}\n'
            f'[gateway]\ncontrol_token = "{control_token}"\n'
            f'[gateway.instance_tokens]\n"{instance.instance_id}" = "{tokens[instance.instance_id]}"\n',
            encoding="utf-8",
        )
    inventory.gateway.config_path.parent.mkdir(parents=True, exist_ok=True)
    inventory.gateway.config_path.write_text(
        '[watch]\ndata_dir = "data"\n'
        f'[gateway]\ncontrol_port = 8810\ncontrol_token = "{control_token}"\n'
        '[gateway.instance_tokens]\n'
        + "".join(f'"{name}" = "{token}"\n' for name, token in tokens.items())
        + '[router.group_instances]\n'
        + "".join(f'"{group}" = "{name}"\n' for group, name in inventory.group_routes.items())
        + '[router.instance_ports]\n'
        + "".join(f'"{i.instance_id}" = {i.listen_ports["bot"]}\n' for i in inventory.instances if i.enabled),
        encoding="utf-8",
    )
    return inventory


def gateway_plan(inventory, *, branch="main", sha=NEW_SHA, note=NOTE):
    return plan_deployment(branch, sha, GATEWAY_CHANGE, inventory=inventory, release_note=note)


def feature_requests(router):
    return [request for request in router.requests if request[0] == "feature"]


@pytest.mark.parametrize(
    ("branch", "feature_scope", "units"),
    [
        ("main", ("alpha", "gamma"), ("xl-alpha.service", "xl-gateway.service")),
        ("test", ("beta", "main"), ("xl-main.service", "xl-gateway.service")),
    ],
)
def test_gateway_lifecycle_covers_affected_instances_but_features_follow_branch(
    tmp_path, branch, feature_scope, units,
):
    inventory = inventory_at(tmp_path, branch=branch)
    router = RecordingRouter()
    transaction = make_transaction(tmp_path, router=router, inventory=inventory)

    result = transaction.execute(gateway_plan(inventory, branch=branch))

    assert result.status == "success"
    affected = ("alpha", "beta", "gamma", "main")
    tx_id = transaction.state.load().transaction_id
    assert router.requests == [
        ("lifecycle", affected, START_NOTICE, f"{tx_id}:starting"),
        ("lifecycle", affected, DONE_NOTICE, f"{tx_id}:complete"),
        ("feature", feature_scope, NOTE, f"{tx_id}:release-note"),
    ]
    maintenance_scope = affected if branch == "main" else ("main", "alpha", "beta", "gamma")
    assert ("pause", *maintenance_scope) in router.events
    assert ("drain", *maintenance_scope) in router.events
    assert ("resume", *maintenance_scope) in router.events
    assert ("stop", *units) in transaction.runner.events
    assert transaction.state.load().announcement_scope == affected


def test_empty_feature_scope_finishes_delivery_and_allows_next_deployment(tmp_path):
    inventory = inventory_at(tmp_path, entries=[("alpha", "test", False, True)])
    router = RecordingRouter()
    transaction = make_transaction(tmp_path, router=router, inventory=inventory)

    result = transaction.execute(gateway_plan(inventory))

    assert result.status == "success"
    assert [request[1:3] for request in router.requests] == [
        (("alpha",), START_NOTICE), (("alpha",), DONE_NOTICE),
    ]
    assert transaction.state.load().release_note_sent is True
    request_count = len(router.requests)
    assert transaction.recover().status == "already_recovered"
    assert len(router.requests) == request_count
    assert transaction.execute(gateway_plan(inventory, sha="5" * 40)).status == "success"
    assert not feature_requests(router)


def test_failed_feature_delivery_retries_same_id_without_stopping_healthy_services(tmp_path):
    inventory = inventory_at(tmp_path)
    router = RecordingRouter()
    router.release_note_results = [False, True]
    transaction = make_transaction(tmp_path, router=router, inventory=inventory)

    assert transaction.execute(gateway_plan(inventory)).status == "announcement_pending"
    journal = transaction.state.load()
    assert journal.phase == "release_note_pending"
    assert journal.completion_notice_sent is True
    candidate = branch_current(transaction, "main")
    runner_events = list(transaction.runner.events)
    requests = list(router.requests)

    assert transaction.recover().status == "success"

    assert feature_requests(router) == [
        ("feature", ("alpha", "gamma"), NOTE, f"{journal.transaction_id}:release-note"),
    ] * 2
    assert router.requests == requests + [requests[-1]]
    assert transaction.runner.events == runner_events + [("health", *journal.impacted_units)]
    assert branch_current(transaction, "main") == candidate
    assert transaction.state.load().release_note_sent is True


def test_completion_retry_keeps_affected_scope_then_delivers_branch_features(tmp_path):
    inventory = inventory_at(tmp_path)
    router = RecordingRouter()
    router.announce_results = [True, False, True]
    transaction = make_transaction(tmp_path, router=router, inventory=inventory)

    assert transaction.execute(gateway_plan(inventory)).status == "announcement_pending"
    journal = transaction.state.load()
    assert journal.phase == "completion_notice_pending"
    runner_events = list(transaction.runner.events)
    failed_completion = router.requests[-1]
    assert not feature_requests(router)

    assert transaction.recover().status == "success"

    assert router.requests[-2] == failed_completion
    assert failed_completion[1:3] == (("alpha", "beta", "gamma", "main"), DONE_NOTICE)
    assert feature_requests(router) == [
        ("feature", ("alpha", "gamma"), NOTE, f"{journal.transaction_id}:release-note"),
    ]
    assert transaction.runner.events == runner_events + [("health", *journal.impacted_units)]


def pending_journal(transaction, *, branch="main", phase="release_note_pending", scope=None):
    """Persist a genuine accepted journal shape with an active healthy candidate."""
    from xl_deploy.state import Journal

    candidate = transaction.paths.releases_root / NEW_SHA
    candidate.mkdir()
    previous = branch_current(transaction, branch)
    journal = Journal(
        transaction_id="old-transaction", branch=branch, sha=NEW_SHA, phase=phase,
        previous_release=str(previous), candidate_release=str(candidate),
        impacted_units=("xl-gateway.service",),
        impacted_tiers=("alpha", "beta", "gamma", "main"),
        announcement_scope=scope if scope is not None else ("alpha", "beta", "gamma", "main"),
        maintenance_enabled=phase == "resuming", release_note=NOTE,
        completion_notice_sent=phase == "release_note_pending", release_note_sent=False,
    )
    transaction.state.set_current(candidate, current_path=transaction.paths.current_path / branch)
    transaction.state.save(journal)
    return journal


@pytest.mark.parametrize("phase", [
    "resuming", "announcing_completion", "completion_notice_pending",
    "release_note_pending", "completion_notice_failed",
])
@pytest.mark.parametrize("generation", [2, 3])
def test_old_pending_journals_use_persisted_branch_at_every_recovery_entry(tmp_path, phase, generation):
    inventory = inventory_at(tmp_path)
    router = RecordingRouter()
    transaction = make_transaction(tmp_path, router=router, inventory=inventory)
    journal = pending_journal(transaction, branch="test", phase=phase)
    payload = json.loads(transaction.state.journal_path.read_text(encoding="utf-8"))
    if generation == 2:
        del payload["recovery_notice_sent"]
    transaction.state.journal_path.write_text(json.dumps(payload), encoding="utf-8")

    assert transaction.recover().status == "success"

    assert feature_requests(router) == [
        ("feature", ("beta", "main"), NOTE, f"{journal.transaction_id}:release-note"),
    ]
    lifecycle = [request for request in router.requests if request[0] == "lifecycle"]
    assert lifecycle == ([] if phase == "release_note_pending" else [
        ("lifecycle", journal.announcement_scope, DONE_NOTICE, f"{journal.transaction_id}:complete"),
    ])
    assert not any(event[0] in {"stop", "start"} for event in transaction.runner.events)
    request_count = len(router.requests)
    assert transaction.recover().status == "already_recovered"
    assert len(router.requests) == request_count


def test_oldest_journal_does_not_resurrect_pre_delivery_tracking_features(tmp_path):
    router = RecordingRouter()
    transaction = make_transaction(tmp_path, router=router, inventory=inventory_at(tmp_path))
    pending_journal(transaction, phase="announcing_completion")
    payload = json.loads(transaction.state.journal_path.read_text(encoding="utf-8"))
    for field in ("release_note", "completion_notice_sent", "release_note_sent", "recovery_notice_sent"):
        del payload[field]
    transaction.state.journal_path.write_text(json.dumps(payload), encoding="utf-8")

    assert transaction.recover().status == "success"

    assert not feature_requests(router)
    assert router.requests[0][1:3] == (("alpha", "beta", "gamma", "main"), DONE_NOTICE)


@pytest.mark.parametrize("empty", [False, True])
def test_pending_feature_uses_current_inventory_without_expanding_original_scope(tmp_path, empty):
    inventory = inventory_at(tmp_path)
    router = RecordingRouter()
    transaction = make_transaction(tmp_path, router=router, inventory=inventory)
    journal = pending_journal(transaction, scope=("gamma", "alpha", "beta", "main"))
    # A newly eligible same-branch instance and disabled prior recipient must not leak in.
    changed = inventory_at(tmp_path, entries=[
        ("alpha", "main", True, False),
        ("beta", "test", False, True),
        ("gamma", "test" if empty else "main", False, True),
        ("main", "test", True, True),
        ("newcomer", "main", True, True),
    ])
    transaction.inventory = changed

    assert transaction.recover().status == "success"

    assert feature_requests(router) == ([] if empty else [
        ("feature", ("gamma",), NOTE, f"{journal.transaction_id}:release-note"),
    ])
    assert transaction.state.load().announcement_scope == journal.announcement_scope
    assert transaction.state.load().release_note_sent is True


def test_pending_feature_preserves_original_recipient_order(tmp_path):
    router = RecordingRouter()
    transaction = make_transaction(tmp_path, router=router, inventory=inventory_at(tmp_path))
    pending_journal(transaction, scope=("gamma", "beta", "alpha"))

    assert transaction.recover().status == "success"

    assert feature_requests(router)[0][1] == ("gamma", "alpha")


@pytest.mark.parametrize("pending", [False, True])
def test_rollback_and_recovery_retry_keep_all_affected_instances_without_feature(tmp_path, pending):
    inventory = inventory_at(tmp_path)
    router = RecordingRouter()
    router.announce_results = [True, False, True]
    transaction = make_transaction(tmp_path, router=router, inventory=inventory)
    if pending:
        journal = pending_journal(transaction)
        # No start notice in this path; the first recovery delivery fails.
        router.announce_results = [False, True]
    else:
        journal = None
    transaction.runner.fail_health_for.add("xl-gateway.service")

    result = transaction.recover() if pending else transaction.execute(gateway_plan(inventory))

    assert result.status == "announcement_pending"
    journal = journal or transaction.state.load()
    assert transaction.state.load().phase == "recovery_notice_pending"
    assert branch_current(transaction, "main") == Path(journal.previous_release)
    assert transaction.recover().status == "rolled_back"
    recovery = [request for request in router.requests if request[2] == RECOVERY_NOTICE]
    assert recovery == [
        ("lifecycle", ("alpha", "beta", "gamma", "main"), RECOVERY_NOTICE,
         f"{journal.transaction_id}:recovery"),
    ] * 2
    assert not feature_requests(router)
    assert not router.maintenance
    assert transaction.recover().status == "already_recovered"


@pytest.mark.parametrize(("branch", "expected"), [
    ("main", ("main",)), ("test", ("debug", "test")), ("debug", ("debug",)),
])
def test_legacy_pending_feature_recipient_policy(tmp_path, branch, expected):
    router = RecordingRouter()
    transaction = make_transaction(tmp_path, router=router)
    pending_journal(transaction, branch=branch, scope=("debug", "test", "main"))

    assert transaction.recover().status == "success"

    assert feature_requests(router)[0][1] == expected


@pytest.mark.parametrize("note", [None, "", "   "])
def test_absent_notes_still_emit_only_affected_lifecycle(tmp_path, note):
    inventory = inventory_at(tmp_path)
    router = RecordingRouter()
    transaction = make_transaction(tmp_path, router=router, inventory=inventory)

    assert transaction.execute(gateway_plan(inventory, note=note)).status == "success"

    assert not feature_requests(router)
    assert [request[2] for request in router.requests] == [START_NOTICE, DONE_NOTICE]


def test_already_delivered_pending_feature_is_not_replayed(tmp_path):
    router = RecordingRouter()
    transaction = make_transaction(tmp_path, router=router, inventory=inventory_at(tmp_path))
    journal = pending_journal(transaction)
    transaction.state.save(replace(journal, release_note_sent=True))

    assert transaction.recover().status == "success"

    assert not router.requests
