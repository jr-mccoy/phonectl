"""Single-writer action funnel for mutating UI operations."""
from __future__ import annotations

import uuid

from phonectl import audit, config, errors, observer, results


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
    gen_id=_new_request_id,
    kill_switch=audit.kill_switch_active,
    log=audit.log_action,
) -> dict:
    cfg = config.load() if cfg is None else cfg
    rid = request_id or gen_id()
    base = {"verb": verb, "target": target, "request_id": rid}

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
