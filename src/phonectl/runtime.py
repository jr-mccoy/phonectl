"""Single-writer action funnel for mutating UI operations."""
from __future__ import annotations

import json
import threading
import time
import uuid

from phonectl import audit, config, errors, observer, policy, ratelimit, results

_action_lock = threading.Lock()
_idempotency_cache: dict = {}   # key -> (ts, env)


def _sweep_idempotency_cache(now_ts, ttl):
    expired = [k for k, (ts, _env) in _idempotency_cache.items() if now_ts - ts >= ttl]
    for k in expired:
        del _idempotency_cache[k]


DEFAULT_LIMITS = {
    "tap": 120,
    "type": 30,
    "swipe": 120,
    "key": 120,
    "launch": 20,
    "high_risk": 1,
    "global": 180,
}


def _new_request_id() -> str:
    return uuid.uuid4().hex


def _rate_path():
    return config.config_dir() / "ratelimit.json"


def _load_rate():
    path = _rate_path()
    return json.loads(path.read_text()) if path.exists() else []


def _save_rate(history) -> None:
    _rate_path().write_text(json.dumps(history))


def _blocked_result(session) -> dict:
    snap = session.last or {}
    return {"app": snap.get("app", {}), "hash": snap.get("hash", "")}


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
    now=time.time,
    companion_transport=None,
) -> dict:
    cfg = config.load() if cfg is None else cfg
    ttl = cfg.get("idempotency_ttl", 300.0)
    if idempotency_key is not None and idempotency_key in _idempotency_cache:
        ts, cached = _idempotency_cache[idempotency_key]
        if now() - ts < ttl:
            replay = dict(cached)
            replay["idempotent_replay"] = True
            return replay
        del _idempotency_cache[idempotency_key]   # expired -> fall through and re-execute
    rid = request_id or gen_id()
    base = {"verb": verb, "target": target, "request_id": rid}

    extra_checks = []
    if companion_transport is not None:
        from phonectl import trust as _trust
        _t = companion_transport
        extra_checks.append(lambda: _trust.companion_stopped(_t))

    env = _run_action_body(
        verb,
        fn,
        target,
        build,
        yes,
        cfg,
        base,
        kill_switch=kill_switch,
        extra_checks=extra_checks,
        log=log,
        now=now,
    )
    if idempotency_key is not None:
        ts_now = now()
        _sweep_idempotency_cache(ts_now, ttl)
        _idempotency_cache[idempotency_key] = (ts_now, dict(env))
    return env


def _run_action_body(
    verb, fn, target, build, yes, cfg, base, *, kill_switch, extra_checks=(), log, now
) -> dict:
    rid = base["request_id"]

    if kill_switch(extra_checks=extra_checks):
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
            decision = policy.explain(session.last, verb, target, cfg)
            risk = {
                "risk_level": decision["risk_level"],
                "reasons": decision["reasons"],
            }
            if decision["decision"] == "deny":
                log(
                    verb,
                    target,
                    _blocked_result(session),
                    request_id=rid,
                    cfg=cfg,
                    outcome="blocked",
                )
                return results.err(
                    errors.GuardedActionError(
                        f"{verb} blocked: risk={decision['risk_level']}"
                    ),
                    user_action=decision["recommended_action"],
                    **risk,
                    **base,
                )
            if decision["decision"] == "confirm" and not yes:
                log(
                    verb,
                    target,
                    _blocked_result(session),
                    request_id=rid,
                    cfg=cfg,
                    outcome="blocked",
                )
                return results.err(
                    errors.ConfirmationRequiredError(
                        f"{verb} needs confirmation: risk={decision['risk_level']}"
                    ),
                    user_action="Re-run with --yes to confirm this action.",
                    **risk,
                    **base,
                )
            ts = now()
            history = ratelimit.prune(_load_rate(), ts)
            allowed, bucket = ratelimit.check(
                history, verb, risk["risk_level"], cfg.get("rate_limits", DEFAULT_LIMITS), ts
            )
            if not allowed:
                log(
                    verb,
                    target,
                    _blocked_result(session),
                    request_id=rid,
                    cfg=cfg,
                    outcome="blocked",
                )
                return results.err(
                    errors.RateLimitError(f"rate limit exceeded for {bucket}"),
                    bucket=bucket,
                    **risk,
                    **base,
                )
            if mode == "dry-run":
                provider = getattr(backend, "last_used", None) or "adb"
                return results.ok(
                    capability=f"ui.{verb}",
                    provider=provider,
                    data=session.last,
                    dry_run=True,
                    **risk,
                    **base,
                )
            snap = fn(backend, session)
            provider = getattr(backend, "last_used", None) or "adb"
            for bucket in ratelimit.buckets_for(verb, risk["risk_level"]):
                history.append({"bucket": bucket, "ts": ts})
            _save_rate(history)
            log(verb, target, snap, request_id=rid, cfg=cfg)
            return results.ok(
                capability=f"ui.{verb}", provider=provider, data=snap, **risk, **base
            )
        except errors.PhonectlError as e:
            return results.err(e, **getattr(e, "lock_state", {}), **base)
    finally:
        _action_lock.release()
