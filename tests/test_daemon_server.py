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
    assert "stop" in rpc_mod.MUTATING and "resume" in rpc_mod.MUTATING


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


def test_macro_run_executes_actions(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    srv = _srv(tmp_path)
    resp = json.loads(srv.handle_line(_req("macro_run",
        {"macro": {"name": "m", "actions": [{"type": "tap", "target": {"i": 0}, "i": 0}]}})))
    assert resp["ok"] is True and resp["data"]["run_id"].startswith("run_")


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
