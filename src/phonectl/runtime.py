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


# --- cross-process idempotency + single-writer lock (Finding 11) ---
# The module globals above only protect a long-lived process (the daemon). Each one-shot CLI
# invocation is a fresh process, so idempotency keys and the single-writer lock must also hold
# through $PHONECTL_HOME: a JSON store for replays and an flock'd sentinel for mutual exclusion.

def _idempotency_path():
    return config.config_dir() / "idempotency.json"


def _load_idempotency() -> dict:
    path = _idempotency_path()
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        return {}   # corrupt/unreadable store must never block actions


def _idempotency_lookup(key, now_ts, ttl):
    """A fresh (unexpired) cached envelope for ``key``, from memory or disk, else None."""
    if key in _idempotency_cache:
        ts, env = _idempotency_cache[key]
        if now_ts - ts < ttl:
            return env
        del _idempotency_cache[key]   # expired -> fall through and re-execute
    entry = _load_idempotency().get(key)
    if entry:
        ts, env = entry
        if now_ts - ts < ttl:
            _idempotency_cache[key] = (ts, env)
            return env
    return None


def _idempotency_store(key, now_ts, env, ttl) -> None:
    _sweep_idempotency_cache(now_ts, ttl)
    _idempotency_cache[key] = (now_ts, dict(env))
    store = {k: v for k, v in _load_idempotency().items() if now_ts - v[0] < ttl}
    store[key] = [now_ts, dict(env)]
    try:
        _idempotency_path().write_text(json.dumps(store))
    except OSError:
        pass   # persistence is best-effort; the in-process cache still holds


def _acquire_file_lock():
    """Cross-process single-writer lock. Returns the open file holding the flock,
    True where flock is unsupported (the thread lock still guards in-process),
    or None when another phonectl process holds the lock."""
    try:
        import fcntl
    except ImportError:
        return True
    f = open(config.config_dir() / "action.lock", "a+")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None


def _release_file_lock(lock) -> None:
    if hasattr(lock, "close"):
        lock.close()   # closing drops the flock


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


def _companion_transport_from_cfg(cfg):
    """A configured companion (companion_port set) is consulted for its STOP
    flag on every action, without each caller having to wire the transport."""
    port = cfg.get("companion_port")
    if not port:
        return None
    from phonectl.providers.transport import SocketTransport
    return SocketTransport(cfg.get("companion_host", "127.0.0.1"), int(port),
                           token=cfg.get("companion_token"))


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
    parent_task_id=None,
    gen_id=_new_request_id,
    kill_switch=audit.kill_switch_active,
    log=audit.log_action,
    now=time.time,
    companion_transport=None,
) -> dict:
    cfg = config.load() if cfg is None else cfg
    ttl = cfg.get("idempotency_ttl", 300.0)
    if idempotency_key is not None:
        cached = _idempotency_lookup(idempotency_key, now(), ttl)
        if cached is not None:
            replay = dict(cached)
            replay["idempotent_replay"] = True
            return replay
    rid = request_id or gen_id()
    base = {"verb": verb, "target": target, "request_id": rid}

    if companion_transport is None:
        companion_transport = _companion_transport_from_cfg(cfg)

    extra_checks = []
    if companion_transport is not None:
        from phonectl import trust as _trust
        _t = companion_transport
        _timeout = cfg.get("companion_timeout", 1.0)
        extra_checks.append(lambda: _trust.companion_stopped(_t, timeout=_timeout))

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
        _idempotency_store(idempotency_key, now(), env, ttl)
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
    file_lock = _acquire_file_lock()
    if file_lock is None:
        _action_lock.release()
        return results.err(
            errors.BusyError("another phonectl process is already running an action"), **base
        )
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
            # Finding 13: when the registry fell through to a lower-priority provider, say so —
            # the agent must be able to detect a provider switch (coordinate semantics differ).
            fallback = list(getattr(backend, "last_fallback", []) or [])
            extra = {"provider_fallback": fallback} if fallback else {}
            for bucket in ratelimit.buckets_for(verb, risk["risk_level"]):
                history.append({"bucket": bucket, "ts": ts})
            _save_rate(history)
            log(verb, target, snap, request_id=rid, cfg=cfg)
            return results.ok(
                capability=f"ui.{verb}", provider=provider, data=snap, **extra, **risk, **base
            )
        except errors.PhonectlError as e:
            return results.err(e, **getattr(e, "lock_state", {}), **base)
    finally:
        _release_file_lock(file_lock)
        _action_lock.release()
