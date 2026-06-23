from datetime import datetime

from phonectl.macro import scheduler as S


def test_interval_next_fire():
    assert S.next_fire({"type": "schedule.interval", "every_seconds": 300},
                       now=datetime(2026, 6, 22, 12, 0, 0)) == 300


def test_time_today_later():
    # 12:00 now, fire at 18:30 → 6h30m = 23400s
    d = S.next_fire({"type": "schedule.time", "at": "18:30"}, now=datetime(2026, 6, 22, 12, 0, 0))
    assert d == 23400


def test_time_already_passed_rolls_to_tomorrow():
    d = S.next_fire({"type": "schedule.time", "at": "08:00"}, now=datetime(2026, 6, 22, 12, 0, 0))
    assert d == (24 - 4) * 3600  # 20h


def test_non_schedule_is_none():
    assert S.next_fire({"type": "notification.posted"}, now=datetime(2026, 6, 22, 12, 0, 0)) is None


def test_validate_bad_time():
    assert S.validate({"type": "schedule.time", "at": "99:99"})
    assert S.validate({"type": "schedule.interval", "every_seconds": 0})
    assert S.validate({"type": "schedule.time", "at": "08:00"}) == []
