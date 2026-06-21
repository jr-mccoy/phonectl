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
