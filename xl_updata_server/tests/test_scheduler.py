from datetime import datetime, timedelta, timezone

from server_app.scheduler import PollScheduler, ScheduleState

TZ = timezone(timedelta(hours=8))


def test_normal_mode_is_due_hourly():
    scheduler = PollScheduler(3600, 60, 1200, datetime(2026, 9, 18, 10, tzinfo=TZ))
    state = ScheduleState(last_check=datetime(2026, 9, 17, 9, tzinfo=TZ))
    assert scheduler.next_due(datetime(2026, 9, 17, 10, tzinfo=TZ), state) == datetime(2026, 9, 17, 10, tzinfo=TZ)


def test_burst_has_twenty_minute_bound_and_stops_after_success():
    scheduler = PollScheduler(3600, 60, 1200, datetime(2026, 9, 18, 10, tzinfo=TZ))
    start = datetime(2026, 9, 18, 10, tzinfo=TZ)
    state = ScheduleState(last_check=start, burst_started_at=start)
    assert scheduler.next_due(start.replace(minute=19), state) <= start.replace(minute=19)
    assert scheduler.next_due(start.replace(minute=20), state) > start.replace(minute=20)
    state = scheduler.record_check(state, start, 123, processed=True)
    assert state.burst_started_at is None
