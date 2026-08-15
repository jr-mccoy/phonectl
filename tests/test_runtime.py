import pytest

from phonectl import config, errors, runtime


class FakeConn:
    def ensure(self):
        pass


class FakeBackend:
    def __init__(self):
        self.taps = []
        self._snap = {"hash": "h1", "app": {"package": "com.x"}, "elements": []}


class FakeSession:
    def __init__(self):
        self.last = None

    def set_snapshot(self, snap):
        self.last = snap


def test_run_action_success_returns_ok_envelope_and_audits(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    backend = FakeBackend()
    sess = FakeSession()
    monkeypatch.setattr(
        runtime.observer,
        "observe",
        lambda b, s, **kw: s.set_snapshot({"hash": "h", "app": {"package": "com.x"}}),
    )

    def build(cfg):
        return backend, sess, FakeConn()

    def fn(b, s):
        b.taps.append((1, 2))
        return {"hash": "after", "app": {"package": "com.x"}}

    env = runtime.run_action(
        "tap", fn, {"x": 1, "y": 2}, build=build, gen_id=lambda: "req123"
    )
    assert env["ok"] is True
    assert env["verb"] == "tap"
    assert env["request_id"] == "req123"
    assert env["data"]["hash"] == "after"
    assert backend.taps == [(1, 2)]
    log = (tmp_path / "actions.jsonl").read_text()
    assert "req123" in log and "tap" in log


def test_run_action_kill_switch_returns_stopped_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    (tmp_path / "STOP").write_text("")
    called = []

    def build(cfg):
        called.append(True)
        raise AssertionError("build must not run when stopped")

    env = runtime.run_action("tap", lambda b, s: None, {"x": 1}, build=build)
    assert env["ok"] is False
    assert env["error"]["code"] == "stopped"
    assert env["error"]["requires_user"] is True
    assert called == []


def test_run_action_confirm_mode_requires_yes(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "confirm"})
    env = runtime.run_action(
        "tap",
        lambda b, s: None,
        {"x": 1},
        build=lambda cfg: (_ for _ in ()).throw(AssertionError("no build")),
        yes=False,
    )
    assert env["error"]["code"] == "confirmation_required"

    backend = FakeBackend()
    sess = FakeSession()
    monkeypatch.setattr(
        runtime.observer,
        "observe",
        lambda b, s, **kw: s.set_snapshot({"hash": "h"}),
    )
    env2 = runtime.run_action(
        "tap",
        lambda b, s: {"hash": "x"},
        {"x": 1},
        build=lambda cfg: (backend, sess, FakeConn()),
        yes=True,
        cfg={"mode": "confirm"},
    )
    assert env2["ok"] is True


def test_run_action_dry_run_observes_but_does_not_act_or_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    backend = FakeBackend()
    sess = FakeSession()
    monkeypatch.setattr(
        runtime.observer,
        "observe",
        lambda b, s, **kw: s.set_snapshot({"hash": "obs"}),
    )
    acted = []
    env = runtime.run_action(
        "tap",
        lambda b, s: acted.append(1),
        {"x": 1},
        build=lambda cfg: (backend, sess, FakeConn()),
        cfg={"mode": "dry-run"},
    )
    assert env["ok"] is True and env["dry_run"] is True
    assert env["data"]["hash"] == "obs"
    assert acted == []
    assert not (tmp_path / "actions.jsonl").exists()


def test_run_action_catches_phonectl_error_into_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    backend = FakeBackend()
    sess = FakeSession()

    def observe(b, s, **kw):
        exc = errors.DeviceLockedError("device is locked, unlock it")
        exc.lock_state = {
            "lock_state": "locked_secure",
            "can_act": False,
            "recommended_user_action": "Unlock the phone manually.",
        }
        raise exc

    monkeypatch.setattr(runtime.observer, "observe", observe)
    env = runtime.run_action(
        "tap", lambda b, s: None, {"x": 1}, build=lambda cfg: (backend, sess, FakeConn())
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "device_locked"
    assert env["lock_state"] == "locked_secure"
    assert env["verb"] == "tap"


def test_run_action_reports_busy_when_lock_held(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    runtime._action_lock.acquire()
    try:
        env = runtime.run_action(
            "tap",
            lambda b, s: None,
            {"x": 1},
            build=lambda cfg: (_ for _ in ()).throw(AssertionError("no build")),
        )
    finally:
        runtime._action_lock.release()
    assert env["ok"] is False
    assert env["error"]["code"] == "busy"
    assert env["error"]["retryable"] is True


def test_run_action_releases_lock_after_success(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    backend = FakeBackend()
    sess = FakeSession()
    monkeypatch.setattr(
        runtime.observer,
        "observe",
        lambda b, s, **kw: s.set_snapshot({"hash": "h"}),
    )
    runtime.run_action(
        "tap",
        lambda b, s: {"hash": "x"},
        {"x": 1},
        build=lambda cfg: (backend, sess, FakeConn()),
    )
    assert runtime._action_lock.acquire(blocking=False) is True
    runtime._action_lock.release()


def test_idempotency_key_replays_first_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    runtime._idempotency_cache.clear()
    backend = FakeBackend()
    sess = FakeSession()
    monkeypatch.setattr(
        runtime.observer,
        "observe",
        lambda b, s, **kw: s.set_snapshot({"hash": "h"}),
    )
    runs = []

    def fn(b, s):
        runs.append(1)
        return {"hash": f"after{len(runs)}"}

    build = lambda cfg: (backend, sess, FakeConn())
    first = runtime.run_action(
        "tap",
        fn,
        {"x": 1},
        build=build,
        idempotency_key="k1",
        gen_id=lambda: "req1",
    )
    second = runtime.run_action(
        "tap",
        fn,
        {"x": 1},
        build=build,
        idempotency_key="k1",
        gen_id=lambda: "req2",
    )
    assert runs == [1]
    assert first["data"]["hash"] == "after1"
    assert second["data"]["hash"] == "after1"
    assert second["idempotent_replay"] is True
    assert second["request_id"] == "req1"


# --- Finding 11: idempotency + the single-writer lock hold across processes ---

def _observed(monkeypatch):
    monkeypatch.setattr(
        runtime.observer,
        "observe",
        lambda b, s, **kw: s.set_snapshot({"hash": "h"}),
    )


def test_idempotency_key_dedupes_across_processes(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    runtime._idempotency_cache.clear()
    _observed(monkeypatch)
    backend, sess = FakeBackend(), FakeSession()
    build = lambda cfg: (backend, sess, FakeConn())
    runs = []

    def fn(b, s):
        runs.append(1)
        return {"hash": f"after{len(runs)}"}

    first = runtime.run_action("tap", fn, {"x": 1}, build=build,
                               idempotency_key="k1", gen_id=lambda: "req1")
    # A one-shot CLI is a fresh process: the module-level cache is empty, only disk survives.
    runtime._idempotency_cache.clear()
    second = runtime.run_action("tap", fn, {"x": 1}, build=build,
                                idempotency_key="k1", gen_id=lambda: "req2")
    assert runs == [1]
    assert second["idempotent_replay"] is True
    assert second["request_id"] == "req1"
    assert second["data"]["hash"] == first["data"]["hash"]
    assert (tmp_path / "idempotency.json").exists()


def test_idempotency_disk_entry_expires_after_ttl(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    runtime._idempotency_cache.clear()
    _observed(monkeypatch)
    backend, sess = FakeBackend(), FakeSession()
    build = lambda cfg: (backend, sess, FakeConn())
    runs = []

    def fn(b, s):
        runs.append(1)
        return {"hash": f"after{len(runs)}"}

    t = [1000.0]
    runtime.run_action("tap", fn, {"x": 1}, build=build, cfg={"mode": "auto"},
                       idempotency_key="k1", gen_id=lambda: "r1", now=lambda: t[0])
    runtime._idempotency_cache.clear()   # fresh process
    t[0] += 301.0                        # past the 300 s default TTL
    env = runtime.run_action("tap", fn, {"x": 1}, build=build, cfg={"mode": "auto"},
                             idempotency_key="k1", gen_id=lambda: "r2", now=lambda: t[0])
    assert runs == [1, 1]
    assert "idempotent_replay" not in env


def test_corrupt_idempotency_file_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    runtime._idempotency_cache.clear()
    (tmp_path / "idempotency.json").write_text("{not json")
    _observed(monkeypatch)
    backend, sess = FakeBackend(), FakeSession()
    env = runtime.run_action("tap", lambda b, s: {"hash": "x"}, {"x": 1},
                             build=lambda cfg: (backend, sess, FakeConn()),
                             idempotency_key="k1")
    assert env["ok"] is True


def test_two_cli_processes_serialize_via_file_lock(tmp_path, monkeypatch):
    # A second phonectl process (simulated by an independently held flock on action.lock)
    # must get a retryable busy envelope, not run concurrently.
    fcntl = pytest.importorskip("fcntl")
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    holder = open(tmp_path / "action.lock", "a+")
    try:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        env = runtime.run_action(
            "tap",
            lambda b, s: None,
            {"x": 1},
            build=lambda cfg: (_ for _ in ()).throw(AssertionError("no build")),
        )
    finally:
        holder.close()
    assert env["ok"] is False
    assert env["error"]["code"] == "busy"
    assert env["error"]["retryable"] is True


def test_file_lock_released_after_action(tmp_path, monkeypatch):
    fcntl = pytest.importorskip("fcntl")
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    _observed(monkeypatch)
    backend, sess = FakeBackend(), FakeSession()
    runtime.run_action("tap", lambda b, s: {"hash": "x"}, {"x": 1},
                       build=lambda cfg: (backend, sess, FakeConn()))
    with open(tmp_path / "action.lock", "a+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # must not raise


def _payment_observe(b, s, **kw):
    s.set_snapshot(
        {
            "hash": "h",
            "app": {"package": "com.x"},
            "elements": [
                {"text": "Confirm payment", "content_desc": "", "password": False}
            ],
        }
    )


def test_run_action_denies_critical_risk(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    backend = FakeBackend()
    sess = FakeSession()
    monkeypatch.setattr(runtime.observer, "observe", _payment_observe)
    acted = []
    env = runtime.run_action(
        "tap",
        lambda b, s: acted.append(1),
        {"i": 0},
        build=lambda cfg: (backend, sess, FakeConn()),
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "guarded_action"
    assert env["risk_level"] == "critical"
    assert acted == []


def test_run_action_high_risk_confirm_requires_yes(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    backend = FakeBackend()
    sess = FakeSession()

    def observe(b, s, **kw):
        s.set_snapshot(
            {
                "hash": "h",
                "app": {"package": "com.x"},
                "elements": [
                    {"text": "", "content_desc": "Password", "password": True}
                ],
            }
        )

    monkeypatch.setattr(runtime.observer, "observe", observe)
    env = runtime.run_action(
        "type",
        lambda b, s: {"hash": "x"},
        {"text": "<x>"},
        build=lambda cfg: (backend, sess, FakeConn()),
        yes=False,
    )
    assert env["error"]["code"] == "confirmation_required"
    assert env["risk_level"] == "high"

    env2 = runtime.run_action(
        "type",
        lambda b, s: {"hash": "x"},
        {"text": "<x>"},
        build=lambda cfg: (backend, sess, FakeConn()),
        yes=True,
    )
    assert env2["ok"] is True and env2["risk_level"] == "high"


def test_run_action_low_risk_success_carries_level(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    backend = FakeBackend()
    sess = FakeSession()
    monkeypatch.setattr(
        runtime.observer,
        "observe",
        lambda b, s, **kw: s.set_snapshot(
            {
                "hash": "h",
                "app": {"package": "com.x"},
                "elements": [{"text": "Wi-Fi"}],
            }
        ),
    )
    env = runtime.run_action(
        "tap",
        lambda b, s: {"hash": "x"},
        {"i": 0},
        build=lambda cfg: (backend, sess, FakeConn()),
    )
    assert env["ok"] is True and env["risk_level"] == "low"


def test_run_action_rate_limits_after_bucket_fills(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto", "rate_limits": {"tap": 1, "global": 100}})
    backend = FakeBackend()
    sess = FakeSession()
    monkeypatch.setattr(
        runtime.observer,
        "observe",
        lambda b, s, **kw: s.set_snapshot(
            {
                "hash": "h",
                "app": {"package": "com.x"},
                "elements": [{"text": "Wi-Fi"}],
            }
        ),
    )
    build = lambda cfg: (backend, sess, FakeConn())
    clock = [1000.0]
    first = runtime.run_action(
        "tap",
        lambda b, s: {"hash": "x"},
        {"i": 0},
        build=build,
        now=lambda: clock[0],
    )
    assert first["ok"] is True
    second = runtime.run_action(
        "tap",
        lambda b, s: {"hash": "x"},
        {"i": 0},
        build=build,
        now=lambda: clock[0],
    )
    assert second["ok"] is False
    assert second["error"]["code"] == "rate_limited"
    assert second["bucket"] == "tap"


def test_rate_history_persisted_and_pruned(tmp_path, monkeypatch):
    import json as _json

    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto", "rate_limits": {"tap": 1, "global": 100}})
    backend = FakeBackend()
    sess = FakeSession()
    monkeypatch.setattr(
        runtime.observer,
        "observe",
        lambda b, s, **kw: s.set_snapshot(
            {
                "hash": "h",
                "app": {"package": "com.x"},
                "elements": [{"text": "Wi-Fi"}],
            }
        ),
    )
    build = lambda cfg: (backend, sess, FakeConn())
    runtime.run_action(
        "tap",
        lambda b, s: {"hash": "x"},
        {"i": 0},
        build=build,
        now=lambda: 1000.0,
    )
    hist = _json.loads((tmp_path / "ratelimit.json").read_text())
    assert any(record["bucket"] == "tap" for record in hist)
    later = runtime.run_action(
        "tap",
        lambda b, s: {"hash": "x"},
        {"i": 0},
        build=build,
        now=lambda: 1120.0,
    )
    assert later["ok"] is True


def test_run_action_reports_provider_from_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import config, capabilities as caps_mod
    from phonectl.providers.registry import ProviderRegistry

    class RegistryFakeBackend:
        serial = "r:5555"
        calls = []
        def get_state(self): return "device"
        def ui_dump(self): return "<hierarchy></hierarchy>"
        def window_dump(self): return ""
        def wm_size(self): return (1080, 2400)
        def input_tap(self, x, y): RegistryFakeBackend.calls.append(("tap", x, y))
        def input_text(self, t): pass
        def input_swipe(self, x1, y1, x2, y2, ms=200): pass
        def input_key(self, k): pass
        def launch(self, pkg): pass
        def screencap(self, path): return path
        def capabilities(self): return caps_mod.make(
            observe_ui_tree=True, act_tap=True, act_type=True, act_key=True,
            launch_app=True, observe_screenshot=True, requires_adb=True,
        )

    fake = RegistryFakeBackend()
    registry = ProviderRegistry([fake])

    def build(cfg):
        from phonectl.session import Session
        from phonectl.connection import Connection
        sess = Session()
        conn = Connection(registry, cfg)
        conn.ensure = lambda: None
        return registry, sess, conn

    monkeypatch.setattr(
        runtime.observer,
        "observe",
        lambda b, s, **kw: s.set_snapshot({"hash": "h", "app": {"package": "com.x"}, "elements": []}),
    )

    def action_fn(b, s):
        b.input_tap(0, 0)
        return {"hash": "after", "app": {}, "elements": []}

    cfg = config.load()
    env = runtime.run_action(
        "tap", action_fn, {"i": 0},
        build=build, yes=True,
        cfg=cfg,
    )
    assert env["ok"] is True
    assert env["provider"] == "RegistryFakeBackend"


def test_run_action_blocked_by_companion_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import config, runtime
    from phonectl.providers.transport import LoopbackTransport
    from phonectl.providers.registry import ProviderRegistry
    from phonectl import capabilities as caps_mod

    class MinimalBackend:
        serial = "fake"
        def get_state(self): return "device"
        def ui_dump(self): return "<hierarchy></hierarchy>"
        def window_dump(self): return ""
        def wm_size(self): return (1080, 2400)
        def input_tap(self, x, y): pass
        def input_text(self, t): pass
        def input_swipe(self, x1, y1, x2, y2, ms=200): pass
        def input_named_swipe(self, d, distance_pct=0.5, ms=400): pass
        def input_long_press(self, x, y, duration_ms=1000): pass
        def input_key(self, k): pass
        def launch(self, pkg): pass
        def screencap(self, path): return path
        def capabilities(self): return caps_mod.make(
            observe_ui_tree=True, act_tap=True, act_type=True, act_key=True,
            launch_app=True, observe_screenshot=True, requires_adb=True,
        )

    stop_transport = LoopbackTransport({"handshake": lambda p: {
        "version": 1, "capabilities": {}, "stopped": True}})
    registry = ProviderRegistry([MinimalBackend()])

    def build(cfg):
        from phonectl.session import Session
        from phonectl.connection import Connection
        sess = Session()
        conn = Connection(registry, cfg)
        conn.ensure = lambda: None
        return registry, sess, conn

    cfg = config.load()
    env = runtime.run_action(
        "tap", lambda b, s: {}, "i=0", build=build, yes=True,
        cfg=cfg, companion_transport=stop_transport,
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "stopped"


def test_blocked_action_is_audited(tmp_path, monkeypatch):
    import json as _json

    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    backend = FakeBackend()
    sess = FakeSession()
    monkeypatch.setattr(runtime.observer, "observe", _payment_observe)
    runtime.run_action(
        "tap",
        lambda b, s: None,
        {"i": 0},
        build=lambda cfg: (backend, sess, FakeConn()),
    )
    rec = _json.loads((tmp_path / "actions.jsonl").read_text().strip().splitlines()[-1])
    assert rec["outcome"] == "blocked" and rec["verb"] == "tap"


def test_idempotency_key_reexecutes_after_ttl(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    runtime._idempotency_cache.clear()
    backend = FakeBackend()
    sess = FakeSession()
    monkeypatch.setattr(runtime.observer, "observe",
                        lambda b, s, **kw: s.set_snapshot({"hash": "h"}))
    runs = []

    def fn(b, s):
        runs.append(1)
        return {"hash": f"after{len(runs)}"}

    build = lambda cfg: (backend, sess, FakeConn())
    clock = {"t": 1000.0}

    first = runtime.run_action("tap", fn, {"x": 1}, build=build,
                               idempotency_key="k1", gen_id=lambda: "r1",
                               now=lambda: clock["t"])
    assert runs == [1]
    assert "idempotent_replay" not in first

    clock["t"] = 1400.0            # 400s later, past the 300s default ttl
    second = runtime.run_action("tap", fn, {"x": 1}, build=build,
                                idempotency_key="k1", gen_id=lambda: "r2",
                                now=lambda: clock["t"])
    assert runs == [1, 1]                         # re-executed, not replayed
    assert "idempotent_replay" not in second


def test_idempotency_cache_sweeps_expired_on_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    runtime._idempotency_cache.clear()
    backend = FakeBackend()
    sess = FakeSession()
    monkeypatch.setattr(runtime.observer, "observe",
                        lambda b, s, **kw: s.set_snapshot({"hash": "h"}))

    def fn(b, s):
        return {"hash": "after"}

    build = lambda cfg: (backend, sess, FakeConn())
    clock = {"t": 0.0}

    # 5 distinct keys, each 1000s apart -> every prior entry is expired when the next stores
    for i in range(5):
        clock["t"] = i * 1000.0
        runtime.run_action("tap", fn, {"x": 1}, build=build,
                           idempotency_key=f"k{i}", gen_id=lambda: f"r{i}",
                           now=lambda: clock["t"])
    assert len(runtime._idempotency_cache) == 1
    assert "k4" in runtime._idempotency_cache


def test_stop_check_failclosed_when_companion_configured_but_unreachable(tmp_path, monkeypatch):
    # Finding 8: kill_switch_active swallows check exceptions, so the check
    # itself must treat "configured but unreachable" as stopped.
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import config as _config
    from phonectl.providers.transport import LoopbackTransport

    unreachable = LoopbackTransport({}, available=False)
    env = runtime.run_action(
        "tap", lambda b, s: {}, "i=0",
        build=lambda cfg: (_ for _ in ()).throw(AssertionError("must not build")),
        yes=True, cfg=_config.load(), companion_transport=unreachable,
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "stopped"


def test_run_action_consults_configured_companion_from_cfg(tmp_path, monkeypatch):
    # A companion configured via companion_port must be consulted by every
    # run_action call even when the caller does not pass companion_transport —
    # otherwise the companion STOP flag never gates CLI/MCP/daemon actions.
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    cfg = {"companion_port": 1, "companion_timeout": 0.05}  # nothing listens on port 1
    env = runtime.run_action(
        "tap", lambda b, s: {}, "i=0",
        build=lambda cfg: (_ for _ in ()).throw(AssertionError("must not build")),
        yes=True, cfg=cfg,
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "stopped"


# ── action_observe_ttl: opt-in reuse of a fresh pre-action snapshot ──────────

def _observe_counter(monkeypatch):
    calls = []
    monkeypatch.setattr(
        runtime.observer, "observe",
        lambda b, s, **kw: (calls.append(1),
                            s.set_snapshot({"hash": "h", "app": {"package": "com.x"},
                                            "observed_at": 1000.0}))[1],
    )
    return calls


def test_action_observe_ttl_reuses_fresh_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    calls = _observe_counter(monkeypatch)
    sess = FakeSession()
    sess.last = {"hash": "h", "app": {"package": "com.x"}, "observed_at": 1000.0}
    env = runtime.run_action(
        "tap", lambda b, s: {"hash": "after", "app": {"package": "com.x"}}, {"x": 1},
        build=lambda cfg: (FakeBackend(), sess, FakeConn()),
        cfg={"mode": "auto", "action_observe_ttl": 2.0},
        now=lambda: 1001.0,   # snapshot is 1s old, inside the 2s window
    )
    assert env["ok"] is True
    assert calls == []   # policy ran on the still-fresh snapshot


def test_action_observe_ttl_default_always_reobserves(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    calls = _observe_counter(monkeypatch)
    sess = FakeSession()
    sess.last = {"hash": "h", "app": {"package": "com.x"}, "observed_at": 1000.0}
    env = runtime.run_action(
        "tap", lambda b, s: {"hash": "after", "app": {"package": "com.x"}}, {"x": 1},
        build=lambda cfg: (FakeBackend(), sess, FakeConn()),
        cfg={"mode": "auto"},
        now=lambda: 1000.1,
    )
    assert env["ok"] is True
    assert calls == [1]   # ttl unset -> unchanged safe default


def test_action_observe_ttl_stale_snapshot_reobserves(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    calls = _observe_counter(monkeypatch)
    sess = FakeSession()
    sess.last = {"hash": "h", "app": {"package": "com.x"}, "observed_at": 1000.0}
    env = runtime.run_action(
        "tap", lambda b, s: {"hash": "after", "app": {"package": "com.x"}}, {"x": 1},
        build=lambda cfg: (FakeBackend(), sess, FakeConn()),
        cfg={"mode": "auto", "action_observe_ttl": 2.0},
        now=lambda: 1010.0,   # 10s old > 2s window
    )
    assert env["ok"] is True
    assert calls == [1]


def test_action_observe_ttl_no_snapshot_reobserves(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    calls = _observe_counter(monkeypatch)
    sess = FakeSession()   # sess.last is None
    env = runtime.run_action(
        "tap", lambda b, s: {"hash": "after", "app": {"package": "com.x"}}, {"x": 1},
        build=lambda cfg: (FakeBackend(), sess, FakeConn()),
        cfg={"mode": "auto", "action_observe_ttl": 2.0},
        now=lambda: 1001.0,
    )
    assert env["ok"] is True
    assert calls == [1]


# ── companion transport reuse: one persistent transport per (host,port,token) ─

def test_companion_transport_reused_across_actions(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    import phonectl.providers.transport as tmod
    made = []

    class FakeTransport:
        def __init__(self, host, port, token=None):
            made.append((host, port, token))

        def request(self, method, params, *, request_id, timeout):
            return {"ok": True, "request_id": request_id,
                    "data": {"version": 1, "capabilities": {}, "stopped": False}}

    monkeypatch.setattr(tmod, "SocketTransport", FakeTransport)
    runtime._transport_cache.clear()
    monkeypatch.setattr(
        runtime.observer, "observe",
        lambda b, s, **kw: s.set_snapshot({"hash": "h", "app": {"package": "com.x"}}),
    )
    cfg = {"mode": "auto", "companion_port": 8765, "companion_token": "tok"}
    for _ in range(2):
        env = runtime.run_action(
            "tap", lambda b, s: {"hash": "a", "app": {"package": "com.x"}}, {"x": 1},
            build=lambda c: (FakeBackend(), FakeSession(), FakeConn()), cfg=cfg,
        )
        assert env["ok"] is True
    assert made == [("127.0.0.1", 8765, "tok")]   # built once, reused after


# ── Crash-safe state files (audit D1) ──────────────────────────────────────

def test_run_action_survives_a_corrupt_ratelimit_file(tmp_path, monkeypatch):
    # A truncated ratelimit.json (kill -9 / full disk mid-write) used to raise
    # JSONDecodeError straight out of run_action, blocking every action forever.
    # Losing the history is recoverable; a dead CLI is not.
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    (tmp_path / "ratelimit.json").write_text('[{"bucket": "tap", "ts": 1')

    sess = FakeSession()
    monkeypatch.setattr(
        runtime.observer, "observe",
        lambda b, s, **kw: s.set_snapshot({"hash": "h", "app": {"package": "com.x"}}))

    env = runtime.run_action(
        "tap", lambda b, s: {"hash": "after", "app": {"package": "com.x"}},
        {"x": 1, "y": 2},
        build=lambda cfg: (FakeBackend(), sess, FakeConn()), gen_id=lambda: "r1")

    assert env["ok"] is True
    # ...and the corrupt file is replaced by a valid one, so it self-heals.
    import json
    assert isinstance(json.loads((tmp_path / "ratelimit.json").read_text()), list)


def test_run_action_survives_a_corrupt_idempotency_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    (tmp_path / "idempotency.json").write_text("{not json")

    sess = FakeSession()
    monkeypatch.setattr(
        runtime.observer, "observe",
        lambda b, s, **kw: s.set_snapshot({"hash": "h", "app": {"package": "com.x"}}))

    env = runtime.run_action(
        "tap", lambda b, s: {"hash": "after", "app": {"package": "com.x"}},
        {"x": 1, "y": 2},
        build=lambda cfg: (FakeBackend(), sess, FakeConn()), gen_id=lambda: "r1",
        idempotency_key="k1")
    assert env["ok"] is True
