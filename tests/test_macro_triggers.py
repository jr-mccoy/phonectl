import pytest

from phonectl import errors
from phonectl.macro import triggers as T


def _ev(type_, **data):
    return {"seq": 1, "type": type_, "ts": 0.0, "source": "x", "data": data}


def test_classification():
    assert T.is_event_driven({"type": "notification.posted"})
    assert T.is_scheduled({"type": "schedule.time"})
    assert T.is_manual({"type": "manual"})
    assert not T.is_event_driven({"type": "schedule.time"})


def test_notification_package_in_filter():
    spec = {"type": "notification.posted", "filters": {"package_in": ["com.whatsapp", "org.signal"]}}
    assert T.matches(spec, _ev("notification.posted", package="com.whatsapp")) is True
    assert T.matches(spec, _ev("notification.posted", package="com.other")) is False


def test_text_regex_filter():
    spec = {"type": "notification.posted", "filters": {"text_regex": "urgent|asap"}}
    assert T.matches(spec, _ev("notification.posted", text="this is URGENT")) is True
    assert T.matches(spec, _ev("notification.posted", text="hello")) is False


def test_type_mismatch_is_false():
    spec = {"type": "ui.text_appears", "filters": {}}
    assert T.matches(spec, _ev("notification.posted")) is False


def test_scheduled_spec_never_matches_event():
    assert T.matches({"type": "schedule.time", "at": "08:00"}, _ev("notification.posted")) is False


def test_unknown_trigger_type_raises():
    with pytest.raises(errors.TriggerError):
        T.matches({"type": "telepathy"}, _ev("notification.posted"))


def test_min_percent_filter():
    spec = {"type": "power.battery_level", "filters": {"min_percent": 20}}
    assert T.matches(spec, _ev("power.battery_level", percent=15)) is True   # at/below -> fire
    assert T.matches(spec, _ev("power.battery_level", percent=20)) is True
    assert T.matches(spec, _ev("power.battery_level", percent=50)) is False  # above -> no fire
