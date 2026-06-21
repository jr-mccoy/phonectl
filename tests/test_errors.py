import pytest
from phonectl import errors


def test_hierarchy_is_correct():
    assert issubclass(errors.ObserveError, errors.PhonectlError)
    assert issubclass(errors.DeviceLockedError, errors.ObserveError)
    assert issubclass(errors.StaleSnapshotError, errors.PhonectlError)
    assert issubclass(errors.CapabilityUnavailableError, errors.PhonectlError)
    assert issubclass(errors.GuardedActionError, errors.PhonectlError)
    assert issubclass(errors.RateLimitError, errors.PhonectlError)


def test_stable_codes():
    assert errors.DeviceLockedError.code == "device_locked"
    assert errors.StaleSnapshotError.code == "stale_snapshot"
    assert errors.CapabilityUnavailableError.code == "capability_unavailable"
    assert errors.GuardedActionError.code == "guarded_action"
    assert errors.RateLimitError.code == "rate_limited"
    assert errors.ObserveError.code == "observe_failed"


def test_actionable_flags():
    assert errors.DeviceLockedError.requires_user is True
    assert errors.DeviceLockedError.retryable is False
    assert errors.ObserveError.retryable is True
    assert errors.StaleSnapshotError.retryable is True


def test_raisable_with_message_and_caught_as_base():
    with pytest.raises(errors.PhonectlError) as e:
        raise errors.DeviceLockedError("device is locked, unlock it")
    assert "locked" in str(e.value)
    assert e.value.code == "device_locked"


def test_phase2_single_writer_codes_and_flags():
    assert errors.BusyError.code == "busy"
    assert errors.BusyError.retryable is True
    assert errors.StoppedError.code == "stopped"
    assert errors.StoppedError.requires_user is True
    assert errors.ConfirmationRequiredError.code == "confirmation_required"
    assert errors.ConfirmationRequiredError.requires_user is True
    for cls in (errors.BusyError, errors.StoppedError, errors.ConfirmationRequiredError):
        assert issubclass(cls, errors.PhonectlError)
