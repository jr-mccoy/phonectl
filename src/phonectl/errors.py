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


class BusyError(PhonectlError):
    # Another mutating action holds the single-writer lock.
    code = "busy"
    retryable = True


class StoppedError(PhonectlError):
    # Emergency stop: the kill-switch STOP sentinel is present.
    code = "stopped"
    requires_user = True


class ConfirmationRequiredError(PhonectlError):
    # Confirm mode or a future risk-policy confirm without --yes.
    code = "confirmation_required"
    requires_user = True


class DaemonUnreachableError(PhonectlError):
    # No reachable daemon was found; frontends fall back to the in-process path.
    code = "daemon_unreachable"
    retryable = True


class JobTimeoutError(PhonectlError):
    # block-and-poll exceeded act_timeout; the job keeps running server-side.
    # NOT auto-retryable: re-running could double-execute. Reattach via the job id.
    code = "job_timeout"
    retryable = False
    requires_user = True


class UnknownMethodError(PhonectlError):
    # The daemon received an RPC method it has no handler for.
    code = "unknown_method"


class MacroValidationError(PhonectlError):
    code = "macro_invalid"
    requires_user = True


class MacroCancelledError(PhonectlError):
    code = "macro_cancelled"


class TriggerError(PhonectlError):
    code = "trigger_invalid"
    requires_user = True
