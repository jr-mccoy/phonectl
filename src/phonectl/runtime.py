"""Single-writer action funnel for mutating UI operations."""
from __future__ import annotations

import threading
import uuid

from phonectl import audit, config, errors, observer, results

_action_lock = threading.Lock()
_idempotency_cache: dict = {}


def _new_request_id() -> str:
    return uuid.uuid4().hex


def run_action(
    verb,
    fn,
    target,
    *,
    build,
    yes=False,
    cfg=None,
    request_id=None,
    idempotency_key=None,
    gen_id=_new_request_id,
    kill_switch=audit.kill_switch_active,
    log=audit.log_action,
) -> dict:
    if idempotency_key is not None and idempotency_key in _idempotency_cache:
        replay = dict(_idempotency_cache[idempotency_key])
        replay["idempotent_replay"] = True
        return replay

    cfg = config.load() if cfg is None else cfg
    rid = request_id or gen_id()
    base = {"verb": verb, "target": target, "request_id": rid}

    env = _run_action_body(
        verb, fn, target, build, yes, cfg, base, kill_switch=kill_switch, log=log
    )
    if idempotency_key is not None:
        _idempotency_cache[idempotency_key] = dict(env)
    return env


def _run_action_body(
    verb, fn, target, build, yes, cfg, base, *, kill_switch, log
) -> dict:
    rid = base["request_id"]

    if kill_switch():
        return results.err(
            errors.StoppedError("action refused (kill switch STOP present)"),
            user_action="Remove the $PHONECTL_HOME/STOP file to resume.",
            **base,
        )

    mode = config.get_mode(cfg)
    if mode == "confirm" and not yes:
        return results.err(
            errors.ConfirmationRequiredError(f"{verb} {target} requires confirmation"),
            user_action="Re-run with --yes to confirm this action.",
            **base,
        )

    if not _action_lock.acquire(blocking=False):
        return results.err(errors.BusyError("another action is already in progress"), **base)
    try:
        try:
            backend, session, conn = build(cfg)
            conn.ensure()
            observer.observe(backend, session)
            if mode == "dry-run":
                return results.ok(
                    capability=f"ui.{verb}",
                    provider="adb",
                    data=session.last,
                    dry_run=True,
                    **base,
                )
            snap = fn(backend, session)
            log(verb, target, snap, request_id=rid, cfg=cfg)
            return results.ok(
                capability=f"ui.{verb}", provider="adb", data=snap, **base
            )
        except errors.PhonectlError as e:
            return results.err(e, **getattr(e, "lock_state", {}), **base)
    finally:
        _action_lock.release()
