from datetime import datetime, timedelta, timezone

from server_app.daemon import should_poll


def test_daemon_uses_sixty_second_interval_inside_burst():
    tz = timezone(timedelta(hours=8))
    assert should_poll(
        datetime(2026, 9, 18, 10, 1, tzinfo=tz),
        last_check=datetime(2026, 9, 18, 10, tzinfo=tz),
        burst=True,
        interval_seconds=60,
    )
