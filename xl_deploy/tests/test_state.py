from __future__ import annotations

import json
import os
import threading

import pytest

from xl_deploy.state import DeploymentState, Journal


def make_journal(**updates: object) -> Journal:
    values: dict[str, object] = {
        "transaction_id": "tx-1",
        "branch": "debug",
        "sha": "a" * 40,
        "phase": "prepared",
        "previous_release": "/srv/xl/releases/old",
        "candidate_release": "/srv/xl/releases/new",
        "impacted_units": ("xl-qqbot-debug.service",),
        "impacted_tiers": ("debug",),
        "announcement_scope": ("debug",),
        "maintenance_enabled": False,
        "evidence": {},
        "error": None,
    }
    values.update(updates)
    return Journal(**values)  # type: ignore[arg-type]


def test_journal_is_durable_and_round_trips(tmp_path):
    state = DeploymentState(tmp_path / "state", releases_root=tmp_path / "releases")
    journal = make_journal(evidence={"preflight": "passed"})

    state.save(journal)

    assert state.load() == journal
    payload = json.loads(state.journal_path.read_text(encoding="utf-8"))
    assert payload["sha"] == "a" * 40
    assert not list(state.journal_path.parent.glob("*.tmp"))


def test_corrupt_journal_fails_closed(tmp_path):
    state = DeploymentState(tmp_path / "state")
    state.journal_path.parent.mkdir(parents=True)
    state.journal_path.write_text('{"phase":"stopping"}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="journal"):
        state.load()


def test_legacy_journal_without_delivery_fields_remains_recoverable(tmp_path):
    state = DeploymentState(tmp_path / "state")
    journal = make_journal(phase="completed")
    payload = json.loads(json.dumps(journal.__dict__))
    payload.pop("release_note")
    payload.pop("completion_notice_sent")
    payload.pop("release_note_sent")
    payload.pop("recovery_notice_sent")
    state.state_dir.mkdir(parents=True)
    state.journal_path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = state.load()

    assert loaded is not None
    assert loaded.completion_notice_sent is True
    assert loaded.release_note_sent is True


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation may require elevated privileges")
def test_current_release_switch_is_atomic_directory_symlink(tmp_path):
    state = DeploymentState(tmp_path / "state", releases_root=tmp_path / "releases")
    old_release = tmp_path / "releases" / "old"
    new_release = tmp_path / "releases" / "new"
    old_release.mkdir(parents=True)
    new_release.mkdir()

    state.set_current(old_release)
    assert (tmp_path / "state" / "current").is_symlink()
    assert (tmp_path / "state" / "current").is_dir()
    assert state.get_current() == old_release.resolve()
    state.set_current(new_release)
    assert state.get_current() == new_release.resolve()
    assert (tmp_path / "state" / "current").resolve() == new_release.resolve()
    assert old_release.exists(), "switching must retain the previous immutable release"


def test_current_pointer_rejects_paths_outside_release_root(tmp_path):
    state = DeploymentState(tmp_path / "state", releases_root=tmp_path / "releases")
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(ValueError, match="release"):
        state.set_current(outside)


def test_process_lock_excludes_another_thread_and_releases_after_error(tmp_path):
    state = DeploymentState(tmp_path / "state")
    acquired = threading.Event()
    release = threading.Event()

    def hold_lock():
        with state.lock():
            acquired.set()
            release.wait(timeout=2)

    worker = threading.Thread(target=hold_lock)
    worker.start()
    assert acquired.wait(timeout=2)
    with pytest.raises(RuntimeError, match="process lock"), state.lock(blocking=False):
        pass
    release.set()
    worker.join(timeout=2)
    assert not worker.is_alive()

    with state.lock(blocking=False):
        pass
