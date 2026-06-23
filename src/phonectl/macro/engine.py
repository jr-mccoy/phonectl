"""Macro executor: control flow + action steps routed through run_action."""
from __future__ import annotations

import time
import uuid

from phonectl import errors, results
from phonectl.macro import PHONE_VERBS
from phonectl.macro import variables as V


class CancellationToken:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class _Stop(Exception):
    pass


class Engine:
    def __init__(self, *, build=None, run_action=None, now=time.time,
                 sleep=lambda s: None, confirm=lambda msg: False, fn_for=None, cfg=None):
        if build is None:
            from phonectl import cli
            build = cli.build_runtime
        if run_action is None:
            from phonectl import runtime
            run_action = runtime.run_action
        self._build = build
        self._run_action = run_action
        self._now = now
        self._sleep = sleep
        self._confirm = confirm
        self._fn_for = fn_for or self._default_fn_for
        self._cfg = cfg

    def _default_fn_for(self, step, scopes):
        from phonectl import cli
        return cli.macro_fn_for(step, scopes)

    def run(self, macro, *, scopes=None, token=None, trigger="manual", yes=False) -> dict:
        scopes = scopes or V.Scopes(macro=dict(macro.variables))
        token = token or CancellationToken()
        run_id = "run_" + uuid.uuid4().hex
        state = {"run_id": run_id, "steps_run": 0, "outcome": "ok", "ok": True,
                 "started_at": self._now(), "cancelled": False}
        try:
            self._exec_steps(macro.actions, scopes, token, state, yes)
        except _Stop:
            pass
        except errors.MacroCancelledError:
            state["cancelled"] = True
            self._append_summary(macro, state, trigger)
            return results.err(errors.MacroCancelledError(),
                               data={**self._summary(state, scopes)})
        self._append_summary(macro, state, trigger)
        if state["ok"]:
            return results.ok(capability="macro.run", data=self._summary(state, scopes))
        return results.err(("macro_failed", f"step failed: {state['outcome']}"),
                           data=self._summary(state, scopes))

    def _append_summary(self, macro, state, trigger):
        try:
            from phonectl.macro import records as _records
            _records.append(_records.macro_run_record(state, macro, trigger=trigger, now=self._now))
        except Exception:
            pass

    def _summary(self, state, scopes):
        return {"run_id": state["run_id"], "steps_run": state["steps_run"],
                "outcome": state["outcome"], "variables": V.redacted_view(scopes)}

    def _exec_steps(self, steps, scopes, token, state, yes):
        for step in steps:
            if token.cancelled:
                raise errors.MacroCancelledError()
            self._exec_step(step, scopes, token, state, yes)

    def _exec_step(self, step, scopes, token, state, yes):
        t = step["type"]
        if t == "stop":
            raise _Stop()
        if t == "set":
            val = step.get("value")
            if isinstance(val, str):
                val = V.interpolate(val, scopes)
            scopes.set(step["var"], val, step.get("scope", "runtime"))
            return
        if t == "wait":
            self._sleep(step.get("seconds", 0))
            return
        if t == "audit_note":
            self._audit_note(V.interpolate(step.get("text", ""), scopes), state)
            return
        if t in PHONE_VERBS:
            self._exec_action(step, scopes, state, yes)
            return
        # control-flow steps added in Task 5
        from phonectl.macro import conditions
        if t == "if":
            ctx = {"scopes": scopes, "snapshot": None}
            branch = step["then"] if conditions.evaluate(step["condition"], ctx) else step.get("else", [])
            self._exec_steps(branch, scopes, token, state, yes)
            return
        if t == "switch":
            key = V.interpolate(str(step["on"]), scopes)
            body = (step.get("cases") or {}).get(key, step.get("default", []))
            self._exec_steps(body, scopes, token, state, yes)
            return
        if t == "for_each":
            in_val = step["in"]
            if isinstance(in_val, str):
                items = scopes.get(in_val.strip("${}"))
            else:
                items = in_val
            for item in (items or []):
                scopes.set(step["as"], item, "runtime")
                self._exec_steps(step["do"], scopes, token, state, yes)
            return
        if t == "loop":
            ctx = {"scopes": scopes, "snapshot": None}
            i = 0
            cap = step.get("max_iterations", 100)
            while i < cap and conditions.evaluate(step.get("while", {"type": "always"}), ctx):
                self._exec_steps(step["do"], scopes, token, state, yes)
                i += 1
            return
        if t == "retry":
            self._exec_retry(step, scopes, token, state, yes)
            return
        if t == "try":
            try:
                self._exec_steps(step["do"], scopes, token, state, yes)
            except _Stop:
                pass  # preserve outcome; finally still runs
            self._exec_steps(step.get("finally", []), scopes, token, state, yes)
            return
        if t == "confirm":
            msg = V.interpolate(step.get("message", "Proceed?"), scopes)
            if not self._confirm(msg):
                state["ok"] = False
                state["outcome"] = "confirmation_required"
                raise _Stop()
            return
        if t == "race":
            return  # full vocabulary in Plan 6.2
        raise errors.MacroValidationError(f"unsupported step at runtime: {t!r}")

    def _exec_action(self, step, scopes, state, yes):
        verb = step["type"]
        target = self._resolve_target(step, scopes)
        fn = self._fn_for(step, scopes)
        env = self._run_action(verb, fn, target, build=self._build, yes=yes,
                               cfg=self._cfg, request_id=None,
                               idempotency_key=step.get("idempotency_key"),
                               parent_task_id=state["run_id"])
        state["steps_run"] += 1
        if not env.get("ok"):
            state["ok"] = False
            state["outcome"] = env.get("error", {}).get("code", "error")
            raise _Stop()

    def _resolve_target(self, step, scopes):
        target = dict(step.get("target", {}))
        for k, v in list(target.items()):
            if isinstance(v, str):
                target[k] = V.interpolate(v, scopes)
        for k in ("text", "package", "keycode", "selector", "direction"):
            if k in step and k not in target:
                v = step[k]
                target[k] = V.interpolate(v, scopes) if isinstance(v, str) else v
        return target

    def _audit_note(self, text, state):
        try:
            from phonectl import audit
            if hasattr(audit, "log_note"):
                audit.log_note(text)
        except Exception:
            pass

    _RETRYABLE = {"busy", "rate_limited", "observe_failed", "stale_snapshot"}

    def _exec_retry(self, step, scopes, token, state, yes):
        attempts = step.get("max_attempts", 3)
        backoff = step.get("backoff_seconds", 1.0)
        for attempt in range(attempts):
            saved_ok, saved_outcome = state["ok"], state["outcome"]
            try:
                self._exec_steps(step["do"], scopes, token, state, yes)
            except _Stop:
                pass
            if state["ok"]:
                return
            if state["outcome"] not in self._RETRYABLE or attempt == attempts - 1:
                raise _Stop()
            state["ok"], state["outcome"] = saved_ok, saved_outcome
            self._sleep(backoff * (2 ** attempt))
