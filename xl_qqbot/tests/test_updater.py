import json

from bot_app.updater import NoticeStateStore, ServiceMute, UpdateEvent, read_new_events


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


def test_read_new_events_passes_through_portrait_payload(tmp_path):
    path = tmp_path / "update_events.jsonl"
    portrait = {
        "event": "portrait_ready",
        "version": "134381674183448664",
        "character_id": "10000224",
        "skin_id": "1000022402",
        "ordinal": 2,
        "name": "雾铃",
        "path": "portrait_outbox/134381674183448664/10000224_1000022402_ab12cd34ef56.png",
        "sha256": "ab" * 32,
        "width": 2903,
        "height": 2761,
        "at": "2026-11-08T10:00:00+08:00",
    }
    finish = {
        "event": "finish",
        "version": 134381674183448664,
        "new_characters": 2,
        "error": None,
        "at": "2026-11-08T10:05:00+08:00",
        "portraits": {"success": 3, "failed": 1, "skipped": 2},
    }
    path.write_text(
        json.dumps({"event": "start", "version": 134381674183448664}, ensure_ascii=False)
        + "\n"
        + json.dumps(portrait, ensure_ascii=False)
        + "\n"
        + json.dumps(finish, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    events, offset = read_new_events(path, 0)

    assert offset == 3
    assert all(isinstance(event, UpdateEvent) for event in events)
    start, ready, done = events
    # start/finish 保持旧解析行为
    assert start.version == 134381674183448664
    assert done.new_characters == 2 and done.error is None
    # portrait_ready 的 version 是字符串且原样保留
    assert ready.event == "portrait_ready"
    assert ready.version == "134381674183448664"
    # 全量 payload 透传，不再只保留少数字段
    assert ready.payload == portrait
    assert ready.payload["character_id"] == "10000224"
    assert ready.payload["ordinal"] == 2
    assert ready.payload["width"] == 2903
    assert ready.payload["height"] == 2761
    assert ready.payload["at"] == "2026-11-08T10:00:00+08:00"
    # finish 的立绘汇总通过 payload 暴露；旧事件无此字段时为 None
    assert done.portraits == {"success": 3, "failed": 1, "skipped": 2}
    assert start.portraits is None
    assert ready.portraits is None


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
