import json

from bot_app.updater import NoticeStateStore, ServiceMute, read_new_events


def test_read_new_events_from_offset(tmp_path):
    path = tmp_path / "update_events.jsonl"
    path.write_text(
        '{"event": "start", "version": 1, "at": "t1"}\n'
        '{"event": "finish", "version": 1, "new_characters": 2, "error": null, "at": "t2"}\n',
        encoding="utf-8",
    )
    events, offset = read_new_events(path, 0)
    assert len(events) == 2
    assert events[0].event == "start" and events[0].version == 1
    assert events[1].event == "finish" and events[1].new_characters == 2
    assert offset == 2

    events, offset = read_new_events(path, offset)
    assert events == [] and offset == 2


def test_read_new_events_missing_and_bad_lines(tmp_path):
    events, offset = read_new_events(tmp_path / "nope.jsonl", 0)
    assert events == [] and offset == 0
    path = tmp_path / "e.jsonl"
    path.write_text('not json\n{"event": "finish", "version": 9}\n', encoding="utf-8")
    events, offset = read_new_events(path, 0)
    assert len(events) == 1 and events[0].version == 9 and offset == 2


def test_notice_state_store_consumed(tmp_path):
    store = NoticeStateStore(tmp_path)
    assert store.consumed_events == 0
    store.mark_consumed(3)
    assert NoticeStateStore(tmp_path).consumed_events == 3


def test_service_mute_flag():
    mute = ServiceMute()
    assert mute.muted is False
    mute.muted = True
    assert mute.muted is True
