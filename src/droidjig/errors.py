"""Canonical typed-error hierarchy for droidjig.

Every class carries a stable string ``code`` plus ``retryable`` and
``requires_user`` flags so result envelopes and CLI/MCP contracts can surface
errors without raw tracebacks.
"""


class DroidjigError(Exception):
    code = "error"
    retryable = False
    requires_user = False


class ObserveError(DroidjigError):
    code = "observe_failed"
    retryable = True


class DeviceLockedError(ObserveError):
    code = "device_locked"
    retryable = False
    requires_user = True


class StaleSnapshotError(DroidjigError):
    code = "stale_snapshot"
    retryable = True


class CapabilityUnavailableError(DroidjigError):
    code = "capability_unavailable"
    requires_user = True


class GuardedActionError(DroidjigError):
    code = "guarded_action"
    requires_user = True


class RateLimitError(DroidjigError):
    code = "rate_limited"
    retryable = True


class BusyError(DroidjigError):
    # Another mutating action holds the single-writer lock.
    code = "busy"
    retryable = True


class StoppedError(DroidjigError):
    # Emergency stop: the kill-switch STOP sentinel is present.
    code = "stopped"
    requires_user = True


class ConfirmationRequiredError(DroidjigError):
    # Confirm mode or a future risk-policy confirm without --yes.
    code = "confirmation_required"
    requires_user = True


class DaemonUnreachableError(DroidjigError):
    # No reachable daemon was found; frontends fall back to the in-process path.
    code = "daemon_unreachable"
    retryable = True


class JobTimeoutError(DroidjigError):
    # block-and-poll exceeded act_timeout; the job keeps running server-side.
    # NOT auto-retryable: re-running could double-execute. Reattach via the job id.
    code = "job_timeout"
    retryable = False
    requires_user = True


class UnknownMethodError(DroidjigError):
    # The daemon received an RPC method it has no handler for.
    code = "unknown_method"


class UnauthorizedError(DroidjigError):
    # An RPC arrived without the required shared-secret token (Finding 2).
    code = "unauthorized"
    requires_user = True


class MacroValidationError(DroidjigError):
    code = "macro_invalid"
    requires_user = True


class MacroCancelledError(DroidjigError):
    code = "macro_cancelled"


class TriggerError(DroidjigError):
    code = "trigger_invalid"
    requires_user = True
