"""Canonical typed-error hierarchy for phonectl.

Every class carries a stable string ``code`` plus ``retryable`` and
``requires_user`` flags so result envelopes and CLI/MCP contracts can surface
errors without raw tracebacks.
"""


class PhonectlError(Exception):
    code = "error"
    retryable = False
    requires_user = False


class ObserveError(PhonectlError):
    code = "observe_failed"
    retryable = True


class DeviceLockedError(ObserveError):
    code = "device_locked"
    retryable = False
    requires_user = True


class StaleSnapshotError(PhonectlError):
    code = "stale_snapshot"
    retryable = True


class CapabilityUnavailableError(PhonectlError):
    code = "capability_unavailable"
    requires_user = True


class GuardedActionError(PhonectlError):
    code = "guarded_action"
    requires_user = True


class RateLimitError(PhonectlError):
    code = "rate_limited"
    retryable = True
