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
    def input_fling(self, direction, velocity=2000): self.calls.append(("fling", direction))
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


def _submit_run_poll(srv, method, params, rid="j1"):
    """Drive an async job to completion synchronously: submit -> run_next -> poll."""
    acc = json.loads(srv.handle_line(_req(method, params, rid)))
    assert acc["ok"] is True, acc
    job_id = acc["data"]["job_id"]
    assert srv.jobs.run_next() is True
    polled = json.loads(srv.handle_line(_req("job_poll", {"job_id": job_id})))
    return acc, polled


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
    config.save({"mode": "auto"})
    from phonectl.providers.registry import ProviderRegistry
    from phonectl.session import Session

    registry = ProviderRegistry([_FakeBackend()])
    session = Session()
    build_calls = {"n": 0}

    def build(cfg):
        build_calls["n"] += 1
        return registry, session, _FakeConn()

    srv = DaemonServer(config.load(), build=build)
    _acc1, polled1 = _submit_run_poll(srv, "act", {"verb": "tap", "target": {"i": 0}, "i": 0}, rid="x1")
    _acc2, polled2 = _submit_run_poll(srv, "act", {"verb": "tap", "target": {"i": 0}, "i": 0}, rid="x2")
    assert polled1["data"]["result"]["ok"] is True
    assert polled2["data"]["result"]["ok"] is True
    assert build_calls["n"] == 1  # warm triple built once, reused by run_action


def test_act_with_selector_captures_into_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    srv = _srv(tmp_path)
    selector = {"text": "Wi-Fi"}
    _acc, polled = _submit_run_poll(
        srv, "act",
        {"verb": "tap", "target": {"selector": selector}, "selector": selector}, rid="s1")
    assert polled["data"]["result"]["ok"] is True

    # The resolved selector -> matched_i lands in the memory selector-library, keyed by
    # package|app_version|locale (app_version/locale default "?").
    from phonectl.macro import memory
    sels = memory.read("selectors")
    assert "com.x|?|?" in sels
    assert sels["com.x|?|?"]["matched_i"] == 0

    from phonectl.daemon import records
    rec = records.read()[-1]
    assert rec["kind"] == "action"
    assert rec["target"]["matched_i"] == 0
    assert rec["context"]["package"] == "com.x"


def test_transport_rejects_oversized_line(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    import io
    srv = DaemonServer(config.load(), build=lambda cfg: (object(), object(), None))

    class _Duplex:
        def __init__(self, data):
            self._r = io.StringIO(data)
            self.out = io.StringIO()
        def readline(self, *a): return self._r.readline(*a)
        def write(self, s): self.out.write(s)
        def flush(self): pass
        def close(self): pass

    class _Conn:
        def __init__(self, data):
            self.f = _Duplex(data)
            self.closed = False
        def makefile(self, *a, **k): return self.f
        def close(self): self.closed = True

    # A giant line with no newline is refused with request_too_large and the connection dropped —
    # never accumulated in full.
    conn = _Conn("x" * (srv.MAX_LINE + 10))
    srv._serve_conn(conn)
    resp = json.loads(conn.f.out.getvalue().strip())
    assert resp["ok"] is False and resp["error"]["code"] == "request_too_large"
    assert conn.closed is True

    # A normal request on a fresh connection is still served.
    ok_conn = _Conn(_req("ping") + "\n")
    srv._serve_conn(ok_conn)
    resp2 = json.loads(ok_conn.f.out.getvalue().strip())
    assert resp2["ok"] is True and resp2["data"]["pong"] is True


def test_capture_context_uses_injected_resolvers(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl.session import Session
    srv = DaemonServer(config.load(), build=lambda cfg: (object(), object(), None),
                       app_version=lambda package: "2.1", locale=lambda: "en")
    s = Session(); s.last = {"app": {"package": "com.x"}}
    assert srv._capture_context(s) == {"package": "com.x", "app_version": "2.1", "locale": "en"}


# ── Task 5: full handler suite ─────────────────────────────────────────────

def test_capabilities_method(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("capabilities")))
    assert resp["ok"] is True and isinstance(resp["data"], dict)


def test_observe_method_returns_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    _acc, polled = _submit_run_poll(srv, "observe", {})
    assert "hash" in polled["data"]["result"]["data"]


def test_stop_engages_sentinel(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import audit
    srv = _srv(tmp_path)
    assert json.loads(srv.handle_line(_req("stop")))["ok"] is True
    assert audit.kill_switch_active() is True


def test_daemon_resume_not_in_agent_surface(tmp_path, monkeypatch):
    # Finding 1: the daemon exposes no resume RPC — an agent cannot clear its own STOP.
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import audit
    srv = _srv(tmp_path)
    assert not srv.registry.has("resume")
    json.loads(srv.handle_line(_req("stop")))
    resp = json.loads(srv.handle_line(_req("resume")))
    assert resp["ok"] is False
    assert resp["error"]["code"] == "unknown_method"
    # STOP is still engaged — the RPC did not clear it.
    assert audit.kill_switch_active() is True


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


# ── Finding 2: shared-secret token on the daemon RPC ──────────────────────

class _FakeBindSock:
    def getsockname(self): return ("127.0.0.1", 54321)
    def close(self): pass


def _bound_srv(tmp_path):
    srv = _srv(tmp_path)
    srv.bind(server_factory=lambda h: _FakeBindSock())
    return srv


def test_bind_publishes_and_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl.daemon import discovery
    srv = _bound_srv(tmp_path)
    token = discovery.read()["token"]
    assert token and srv._token == token


def test_daemon_rejects_rpc_without_token(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _bound_srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("status")))  # no token field
    assert resp["ok"] is False
    assert resp["error"]["code"] == "unauthorized"


def test_daemon_rejects_rpc_with_wrong_token(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _bound_srv(tmp_path)
    line = json.dumps({"method": "status", "params": {}, "request_id": "r1",
                       "token": "not-the-token"})
    resp = json.loads(srv.handle_line(line))
    assert resp["ok"] is False and resp["error"]["code"] == "unauthorized"


def test_daemon_accepts_rpc_with_token(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _bound_srv(tmp_path)
    line = json.dumps({"method": "status", "params": {}, "request_id": "r1",
                       "token": srv._token})
    resp = json.loads(srv.handle_line(line))
    assert resp["ok"] is True


def test_daemon_ping_is_token_exempt(tmp_path, monkeypatch):
    # ping stays open so discovery can detect a live daemon without the token.
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _bound_srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("ping")))
    assert resp["ok"] is True


def test_daemon_client_carries_token_from_discovery():
    from phonectl.daemon.client import DaemonClient
    # from_discovery reads the token out of the discovery info dict and hands it to
    # the transport, which stamps it onto every request.
    client = DaemonClient.from_discovery(
        {"host": "127.0.0.1", "port": 1, "version": 1, "token": "abc"})
    assert client._transport._token == "abc"


# ── Task 2: observe mints snapshot_id ─────────────────────────────────────

def _observe_line():
    return json.dumps({"method": "observe", "params": {}, "request_id": "r1"})


def test_observe_returns_snapshot_id(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    _acc, polled = _submit_run_poll(srv, "observe", {}, rid="r1")
    assert polled["ok"] is True
    assert polled["data"]["result"]["snapshot_id"] == "snap_1"


def test_second_observe_increments_snapshot_id(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    _acc1, polled1 = _submit_run_poll(srv, "observe", {}, rid="r1")
    _acc2, polled2 = _submit_run_poll(srv, "observe", {}, rid="r2")
    assert polled1["data"]["result"]["snapshot_id"] == "snap_1"
    assert polled2["data"]["result"]["snapshot_id"] == "snap_2"
    assert srv.snapshots.current_id == "snap_2"


# ── Task 3: stale-index protection ────────────────────────────────────────

def test_act_rejects_stale_snapshot_id_before_running(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    _submit_run_poll(srv, "observe", {}, rid="obs1")   # snap_1 (current)
    # Pin a stale id that is no longer current.
    _acc, polled = _submit_run_poll(
        srv, "act",
        {"verb": "tap", "target": "i=0", "snapshot_id": "snap_0", "yes": True},
        rid="r2",
    )
    out = polled["data"]["result"]
    assert out["ok"] is False
    assert out["error"]["code"] == "stale_snapshot"
    assert "re-observe" in out["error"]["user_action"].lower()


# ── Task 4: snapshot_before/after on act + runs.jsonl ─────────────────────

def test_act_returns_before_and_after_snapshot_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    _submit_run_poll(srv, "observe", {}, rid="obs1")   # snap_1 current
    _acc, polled = _submit_run_poll(
        srv, "act",
        {"verb": "tap", "target": "i=0", "yes": True},
        rid="r3",
    )
    out = polled["data"]["result"]
    assert out["ok"] is True
    assert out["snapshot_before"] == "snap_1"
    assert out["snapshot_after"] == "snap_2"
    assert out["snapshot_before"] != out["snapshot_after"]
    assert srv.snapshots.current_id == "snap_2"


def test_act_backfills_runs_jsonl_snapshot_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    _submit_run_poll(srv, "observe", {}, rid="obs1")
    _submit_run_poll(srv, "act", {"verb": "tap", "target": "i=0", "yes": True}, rid="r4")
    runs = (tmp_path / "runs.jsonl").read_text().strip().splitlines()
    rec = json.loads(runs[-1])
    assert rec["snapshot_before"] == "snap_1"
    assert rec["snapshot_after"] == "snap_2"


# ── Task 6: action_started/finished + lifecycle events ────────────────────

def test_act_emits_started_and_finished_events(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    _submit_run_poll(srv, "observe", {}, rid="obs1")
    _submit_run_poll(srv, "act", {"verb": "tap", "target": "i=0", "yes": True}, rid="r5")
    out = srv.events.poll(since=0)
    types = [e["type"] for e in out["events"]]
    assert "action_started" in types
    assert "action_finished" in types
    started = next(e for e in out["events"] if e["type"] == "action_started")
    assert started["data"]["request_id"] == "r5"
    assert started["source"] == "daemon"
    finished = next(e for e in out["events"] if e["type"] == "action_finished")
    assert finished["data"]["ok"] is True


def test_bind_and_shutdown_emit_lifecycle_events(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)

    class FakeServerSock:
        def getsockname(self): return ("127.0.0.1", 54321)
        def close(self): pass

    srv.bind(server_factory=lambda h: FakeServerSock())   # threadless/socketless
    srv.shutdown()
    phases = [e["data"]["phase"] for e in srv.events.poll(since=0)["events"]
              if e["type"] == "lifecycle"]
    assert phases == ["started", "stopped"]


# ── Task 8: events_poll RPC ───────────────────────────────────────────────

def test_events_poll_returns_events_and_cursor(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    _submit_run_poll(srv, "observe", {}, rid="obs1")
    _submit_run_poll(srv, "act", {"verb": "tap", "target": "i=0", "yes": True}, rid="r6")
    out = json.loads(srv.handle_line(json.dumps(
        {"method": "events_poll", "params": {"since": 0}, "request_id": "r7"})))
    assert out["ok"] is True
    assert "events" in out["data"] and "cursor" in out["data"]
    types = [e["type"] for e in out["data"]["events"]]
    assert "action_started" in types and "action_finished" in types
    assert out["data"]["cursor"] == out["data"]["events"][-1]["seq"]


def test_events_poll_since_cursor_returns_only_newer(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    _submit_run_poll(srv, "observe", {}, rid="obs1")
    _submit_run_poll(srv, "act", {"verb": "tap", "target": "i=0", "yes": True}, rid="r8")
    first = json.loads(srv.handle_line(json.dumps(
        {"method": "events_poll", "params": {"since": 0}, "request_id": "r9"})))
    cursor = first["data"]["cursor"]
    again = json.loads(srv.handle_line(json.dumps(
        {"method": "events_poll", "params": {"since": cursor}, "request_id": "r10"})))
    assert again["data"]["events"] == []
    assert again["data"]["cursor"] == cursor


# ── Task 5 new tests: async job submission + job_poll ─────────────────────

def test_act_submit_returns_job_id(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    acc = json.loads(srv.handle_line(_req("act", {"verb": "tap", "target": {"i": 0}, "i": 0})))
    assert acc["ok"] is True
    assert acc["data"]["status"] == "accepted"
    assert isinstance(acc["data"]["job_id"], str) and acc["data"]["job_id"]


def test_act_job_poll_returns_result_when_done(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    srv = _srv(tmp_path)
    _acc, polled = _submit_run_poll(srv, "act", {"verb": "tap", "target": {"i": 0}, "i": 0})
    assert polled["ok"] is True
    assert polled["data"]["status"] == "done"
    assert polled["data"]["result"]["ok"] is True


def test_observe_job_poll_returns_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    _acc, polled = _submit_run_poll(srv, "observe", {})
    assert polled["data"]["status"] == "done"
    assert "hash" in polled["data"]["result"]["data"]
    assert "snapshot_id" in polled["data"]["result"]


def test_job_poll_unknown_id_is_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("job_poll", {"job_id": "nope"})))
    assert resp["ok"] is False
    assert resp["error"]["code"] == "unknown_job"


def test_act_via_worker_appends_one_run_record(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl.daemon import records
    srv = _srv(tmp_path)
    _submit_run_poll(srv, "act", {"verb": "tap", "target": {"i": 0}, "i": 0}, rid="rr")
    rows = records.read()
    assert len(rows) == 1
    assert rows[0]["verb"] == "tap"
    assert rows[0]["request_id"] == "rr"


def test_act_is_not_in_handle_line_mutating_set():
    from phonectl.daemon import rpc as rpc_mod
    assert "act" not in rpc_mod.MUTATING


# ── gesture verbs must route through the daemon act job, not just the 5 core ──

@pytest.mark.parametrize("params, expected_call", [
    ({"verb": "named_swipe", "target": {"direction": "up"}, "direction": "up"},
     ("named_swipe", "up")),
    ({"verb": "scroll", "target": {"direction": "down"}, "direction": "down"},
     ("named_swipe", "down")),
    ({"verb": "long_press", "target": {"i": 0}, "i": 0},
     ("long_press", 540, 450)),
    ({"verb": "drag", "target": {"coords": [1, 2, 3, 4]}, "coords": [1, 2, 3, 4]},
     ("swipe", 1, 2, 3, 4)),
    ({"verb": "fling", "target": {"direction": "up"}, "direction": "up"},
     ("fling", "up")),
])
def test_gesture_verbs_run_through_worker(tmp_path, monkeypatch, params, expected_call):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    backend = _FakeBackend()
    from phonectl.providers.registry import ProviderRegistry
    from phonectl.session import Session
    srv = DaemonServer(config.load(),
                       build=lambda cfg: (ProviderRegistry([backend]), Session(), _FakeConn()))
    _acc, polled = _submit_run_poll(srv, "act", params)
    assert polled["ok"] is True, polled
    assert polled["data"]["status"] == "done", polled
    assert polled["data"]["result"]["ok"] is True, polled["data"]["result"]
    assert expected_call in backend.calls, backend.calls


def test_double_tap_runs_through_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    backend = _FakeBackend()
    from phonectl.providers.registry import ProviderRegistry
    from phonectl.session import Session
    srv = DaemonServer(config.load(),
                       build=lambda cfg: (ProviderRegistry([backend]), Session(), _FakeConn()))
    _acc, polled = _submit_run_poll(
        srv, "act", {"verb": "double_tap", "target": {"i": 0}, "i": 0})
    assert polled["data"]["result"]["ok"] is True
    assert backend.calls.count(("tap", 540, 450)) == 2


def test_scroll_until_runs_through_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    backend = _FakeBackend()
    from phonectl.providers.registry import ProviderRegistry
    from phonectl.session import Session
    srv = DaemonServer(config.load(),
                       build=lambda cfg: (ProviderRegistry([backend]), Session(), _FakeConn()))
    # "Wi-Fi" is present in the fake dump, so scroll_until returns immediately.
    _acc, polled = _submit_run_poll(
        srv, "act",
        {"verb": "scroll_until", "target": {"direction": "down", "text": "Wi-Fi"},
         "direction": "down", "text": "Wi-Fi"})
    assert polled["data"]["result"]["ok"] is True, polled["data"]["result"]
    assert "stop" in rpc_mod.MUTATING
    assert "resume" not in rpc_mod.MUTATING  # Finding 1: no resume RPC


# ── Task 6: shutdown RPC + worker lifecycle ───────────────────────────────

def test_shutdown_rpc_flags_not_running_and_returns_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    srv._running = True
    resp = json.loads(srv.handle_line(_req("shutdown")))
    assert resp["ok"] is True
    assert resp["data"]["stopping"] is True
    assert srv._running is False


def test_shutdown_method_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    srv.shutdown()
    srv.shutdown()  # must not raise


def test_shutdown_in_methods_list(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("status")))
    assert "shutdown" in resp["data"]["methods"]
    assert "job_poll" in resp["data"]["methods"]


# ── Task 8: macro RPC handlers ────────────────────────────────────────────

def test_macro_validate_method(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("macro_validate",
        {"macro": {"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]}})))
    assert resp["ok"] is True and resp["data"]["valid"] is True


def test_macro_validate_reports_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("macro_validate", {"macro": {"actions": []}})))
    assert resp["ok"] is True and resp["data"]["valid"] is False and resp["data"]["errors"]


def test_macro_run_is_async_job(tmp_path, monkeypatch):
    # macro_run must not execute in the handle_line thread: a multi-step macro
    # blocks for minutes, past any client RPC deadline (Finding 2, 2026-07-04).
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    srv = _srv(tmp_path)
    acc, polled = _submit_run_poll(srv, "macro_run",
        {"macro": {"name": "m", "actions": [{"type": "tap", "target": {"i": 0}, "i": 0}]}})
    assert acc["data"]["status"] == "accepted"
    env = polled["data"]["result"]
    assert polled["data"]["status"] == "done"
    assert env["ok"] is True and env["data"]["run_id"].startswith("run_")
    assert env["data"]["steps_run"] == 1


def test_macro_in_mutating_set():
    from phonectl.daemon import rpc
    assert {"macro_run", "macro_cancel"} <= rpc.MUTATING


# ── Task 7: macro_enable / macro_disable / macro_list RPC ────────────────────

def test_macro_enable_disable_list(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    doc = {"name": "m", "trigger": {"type": "clipboard.changed"},
           "actions": [{"type": "tap", "target": {"i": 0}}]}
    assert json.loads(srv.handle_line(_req("macro_enable", {"macro": doc})))["ok"] is True
    listed = json.loads(srv.handle_line(_req("macro_list")))
    assert any(m["name"] == "m" and m["enabled"] for m in listed["data"]["macros"])
    assert json.loads(srv.handle_line(_req("macro_disable", {"name": "m"})))["ok"] is True


# ── Task 9: autonomy + memory RPC handlers ────────────────────────────────

def test_autonomy_grant_list_revoke(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    g = json.loads(srv.handle_line(_req("autonomy_grant", {"macro": "reply", "max_risk": "high"})))
    assert g["ok"] is True
    listed = json.loads(srv.handle_line(_req("autonomy_list")))
    assert any(x["macro"] == "reply" for x in listed["data"]["grants"])
    assert json.loads(srv.handle_line(_req("autonomy_revoke", {"macro": "reply"})))["ok"] is True


def test_memory_show_export_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl.macro import memory
    memory.write("prefs", {"quiet_hours": "22:00-08:00"})
    srv = _srv(tmp_path)
    shown = json.loads(srv.handle_line(_req("memory_show", {"store": "prefs"})))
    assert shown["data"]["quiet_hours"] == "22:00-08:00"
    assert json.loads(srv.handle_line(_req("memory_delete", {"store": "prefs"})))["ok"] is True
