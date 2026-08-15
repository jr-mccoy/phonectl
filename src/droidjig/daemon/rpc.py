"""RPC method registry: name -> handler, dispatch to a results envelope."""
from __future__ import annotations

from droidjig import errors, results

# "resume" is deliberately absent — the daemon exposes no resume RPC (Finding 1);
# clearing the kill switch is a human-only, out-of-band action.
MUTATING = {"stop", "macro_run", "macro_cancel",
            "macro_enable", "macro_disable", "autonomy_grant", "autonomy_revoke", "memory_delete"}


class Registry:
    def __init__(self) -> None:
        self._handlers: dict = {}

    def register(self, name):
        def deco(fn):
            if name in self._handlers:
                raise ValueError(f"duplicate RPC method {name!r}")
            self._handlers[name] = fn
            return fn
        return deco

    def has(self, name) -> bool:
        return name in self._handlers

    def dispatch(self, name, params, ctx) -> dict:
        handler = self._handlers.get(name)
        if handler is None:
            return results.err(
                errors.UnknownMethodError(f"no RPC method {name!r}"),
                user_action="Call a supported method; see daemon status for the method list.",
            )
        try:
            return handler(params or {}, ctx)
        except errors.DroidjigError as e:
            return results.err(e, **getattr(e, "lock_state", {}))
        except Exception as e:
            return results.err(("internal_error", str(e)))
