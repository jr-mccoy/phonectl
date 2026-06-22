import json
import threading

import pytest

from phonectl import capabilities, config, results
from phonectl.daemon import PROTOCOL_VERSION
from phonectl.daemon import rpc as rpc_mod
from phonectl.daemon.server import DaemonServer


# ── shared helpers ─────────────────────────────────────────────────────────

def _req(method, params=None, rid="r1"):
    return json.dumps({"method": method, "params": params or {}, "request_id": rid,
                       "timeout": 2.0, "version": PROTOCOL_VERSION})


class _FakeBackend:
    def __init__(self):
        self.calls = []
        self.serial = "d"
        self._xml = ("""<?xml version='1.0'?><hierarchy rotation="0">"""
                     """<node index="0" text="Wi-Fi" resource-id="android:id/title" class="T" """
                     """content-desc="" clickable="true" bounds="[44,380][1036,520]"/></hierarchy>""")

    def get_state(self): return "device"
    def ui_dump(self): return self._xml
    def window_dump(self): return "mCurrentFocus=Window{a b com.x/.A}"
    def wm_size(self): return (1080, 2400)
    def input_tap(self, x, y): self.calls.append(("tap", x, y))
    def input_text(self, t): self.calls.append(("text", t))
    def input_key(self, k): self.calls.append(("key", k))
    def input_swipe(self, x1, y1, x2, y2, ms=200): self.calls.append(("swipe", x1, y1, x2, y2))
    def input_named_swipe(self, direction, distance_pct=0.5, ms=400): self.calls.append(("named_swipe", direction))
    def input_long_press(self, x, y, duration_ms=1000): self.calls.append(("long_press", x, y))
    def launch(self, pkg): self.calls.append(("launch", pkg))
    def screencap(self, path): return path

    def capabilities(self):
        return capabilities.make(
            observe_ui_tree=True, act_tap=True, act_type=True, act_key=True,
            launch_app=True, observe_screenshot=True, requires_adb=True,
        )


class _FakeConn:
    def ensure(self): pass


def _srv(tmp_path):
    from phonectl.providers.registry import ProviderRegistry
    from phonectl.session import Session

    registry = ProviderRegistry([_FakeBackend()])
    session = Session()
    return DaemonServer(config.load(), build=lambda cfg: (registry, session, _FakeConn()))


# ── Task 3: handle_line basics ─────────────────────────────────────────────

def test_handle_line_ping(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = DaemonServer(config.load(), build=lambda cfg: (object(), object(), None))
    resp = json.loads(srv.handle_line(_req("ping")))
    assert resp["ok"] is True and resp["data"]["pong"] is True
    assert resp["request_id"] == "r1"
    assert resp["version"] == PROTOCOL_VERSION


def test_handle_line_unknown_method(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = DaemonServer(config.load(), build=lambda cfg: (object(), object(), None))
    resp = json.loads(srv.handle_line(_req("does_not_exist")))
    assert resp["ok"] is False and resp["error"]["code"] == "unknown_method"


def test_handle_line_bad_request(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = DaemonServer(config.load(), build=lambda cfg: (object(), object(), None))
    resp = json.loads(srv.handle_line("{not json"))
    assert resp["ok"] is False and resp["error"]["code"] == "bad_request"


def test_non_loopback_daemon_host_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    with pytest.raises(ValueError):
        DaemonServer({"daemon_host": "0.0.0.0"}, build=lambda cfg: (None, None, None))


def test_mutating_method_holds_write_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    # Temporarily add a test-only method to MUTATING so we can register it freely.
    monkeypatch.setattr(rpc_mod, "MUTATING", rpc_mod.MUTATING | {"test_mutating"})
    srv = DaemonServer(config.load(), build=lambda cfg: (object(), object(), None))
    observed = {}

    @srv.registry.register("test_mutating")
    def _test(params, ctx):
        observed["locked"] = srv._write_lock.locked()
        return results.ok(capability="test", data={})

    srv.handle_line(_req("test_mutating"))
    assert observed["locked"] is True


# ── Task 4: warm triple + act via run_action ───────────────────────────────

def test_warm_triple_builds_once(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    calls = {"n": 0}

    def build(cfg):
        calls["n"] += 1
        return ("REG", "SESS", None)

    srv = DaemonServer(config.load(), build=build)
    a = srv._warm_triple()
    b = srv._warm_triple()
    assert a is b
    assert calls["n"] == 1


def test_act_reuses_one_registry_across_two_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl.providers.registry import ProviderRegistry
    from phonectl.session import Session

    registry = ProviderRegistry([_FakeBackend()])
    session = Session()
    build_calls = {"n": 0}

    def build(cfg):
        build_calls["n"] += 1
        return registry, session, _FakeConn()

    srv = DaemonServer(config.load(), build=build)
    rid = "x1"
    line = json.dumps({"method": "act",
                       "params": {"verb": "tap", "target": {"i": 0}, "i": 0},
                       "request_id": rid, "timeout": 2.0, "version": PROTOCOL_VERSION})
    r1 = json.loads(srv.handle_line(line))
    r2 = json.loads(srv.handle_line(line))
    assert r1["ok"] is True and r2["ok"] is True
    assert build_calls["n"] == 1  # warm triple built once, reused by run_action


# ── Task 5: full handler suite ─────────────────────────────────────────────

def test_capabilities_method(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("capabilities")))
    assert resp["ok"] is True and isinstance(resp["data"], dict)


def test_observe_method_returns_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("observe")))
    assert resp["ok"] is True and "hash" in resp["data"]


def test_stop_then_resume_toggles_sentinel(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import audit
    srv = _srv(tmp_path)
    assert json.loads(srv.handle_line(_req("stop")))["ok"] is True
    assert audit.kill_switch_active() is True
    assert json.loads(srv.handle_line(_req("resume")))["ok"] is True
    assert audit.kill_switch_active() is False


def test_audit_query_returns_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("audit_query", {"limit": 5})))
    assert resp["ok"] is True and isinstance(resp["data"], list)


def test_status_method(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("status")))
    assert resp["ok"] is True
    assert resp["data"]["protocol_version"] == PROTOCOL_VERSION
    assert "ping" in resp["data"]["methods"]


# ── Task 6: run records ────────────────────────────────────────────────────

def test_act_appends_run_record(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl.daemon import records
    srv = _srv(tmp_path)
    line = json.dumps({"method": "act", "params": {"verb": "tap", "target": {"i": 0}, "i": 0},
                       "request_id": "rr", "timeout": 2.0, "version": PROTOCOL_VERSION})
    srv.handle_line(line)
    rows = records.read()
    assert len(rows) == 1
    assert rows[0]["verb"] == "tap" and rows[0]["request_id"] == "rr"


# ── Task 9: bind / shutdown lifecycle ─────────────────────────────────────

def test_bind_publishes_daemon_json_and_shutdown_removes_it(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl.daemon import discovery

    class FakeServerSock:
        def __init__(self): self.closed = False
        def getsockname(self): return ("127.0.0.1", 54321)
        def close(self): self.closed = True

    sock = FakeServerSock()
    srv = DaemonServer(config.load(), build=lambda cfg: (object(), object(), None))
    host, port = srv.bind(server_factory=lambda h: sock)
    assert (host, port) == ("127.0.0.1", 54321)
    assert discovery.read()["port"] == 54321
    srv.shutdown()
    assert discovery.read() is None
    assert sock.closed is True
