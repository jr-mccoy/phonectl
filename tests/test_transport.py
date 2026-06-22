import json
import pytest
from phonectl.providers.transport import LoopbackTransport, next_request_id, SocketTransport


class FakeConn:
    """Scriptable newline conn: each request line yields the queued response line(s)."""
    def __init__(self, script):
        self._script = list(script)
        self.sent = []
        self.closed = False

    def sendline(self, s):
        self.sent.append(s)

    def readline(self):
        return self._script.pop(0) if self._script else ""

    def close(self):
        self.closed = True


def _conn_factory(script):
    def factory(host, port, timeout):
        return FakeConn(script)
    return factory


def test_request_echoes_request_id_and_wraps_data():
    t = LoopbackTransport({"echo": lambda p: {"said": p["msg"]}})
    rid = next_request_id()
    resp = t.request("echo", {"msg": "hi"}, request_id=rid, timeout=1.0)
    assert resp["ok"] is True
    assert resp["request_id"] == rid
    assert resp["data"]["said"] == "hi"


def test_request_unknown_method_returns_error_envelope():
    t = LoopbackTransport({})
    resp = t.request("nope", {}, request_id=next_request_id(), timeout=1.0)
    assert resp["ok"] is False
    assert "error" in resp


def test_handler_exception_becomes_error_envelope():
    def boom(p):
        raise RuntimeError("kaboom")
    t = LoopbackTransport({"boom": boom})
    resp = t.request("boom", {}, request_id=next_request_id(), timeout=1.0)
    assert resp["ok"] is False
    assert "kaboom" in resp["error"]["message"]


def test_ping_reflects_availability():
    assert LoopbackTransport({}).ping() is True
    assert LoopbackTransport({}, available=False).ping() is False


def test_next_request_id_is_unique():
    assert next_request_id() != next_request_id()


def test_socket_transport_rejects_non_loopback():
    with pytest.raises(ValueError):
        SocketTransport("10.0.0.5", 8765)


def test_socket_transport_matches_request_id(monkeypatch):
    rid = "fixedid"
    monkeypatch.setattr("phonectl.providers.transport.next_request_id", lambda: rid)
    resp_line = json.dumps({"ok": True, "request_id": rid, "version": 1, "data": {"pong": True}})
    t = SocketTransport("127.0.0.1", 8765, connect=_conn_factory([resp_line + "\n"]))
    out = t.request("ping", {}, request_id=rid, timeout=1.0)
    assert out["ok"] is True and out["data"]["pong"] is True


def test_socket_transport_drops_stale_then_matches(monkeypatch):
    rid = "want"
    stale = json.dumps({"ok": True, "request_id": "other", "version": 1, "data": {}}) + "\n"
    good = json.dumps({"ok": True, "request_id": rid, "version": 1, "data": {"v": 1}}) + "\n"
    t = SocketTransport("127.0.0.1", 8765, connect=_conn_factory([stale, good]))
    out = t.request("m", {}, request_id=rid, timeout=1.0)
    assert out["data"]["v"] == 1


def test_socket_transport_ping_true_on_ok():
    def factory(host, port, timeout):
        class C(FakeConn):
            def readline(self):
                last = self.sent[-1]
                rid = json.loads(last)["request_id"]
                return json.dumps({"ok": True, "request_id": rid, "version": 1, "data": {}}) + "\n"
        return C([])
    t = SocketTransport("127.0.0.1", 8765, connect=factory)
    assert t.ping() is True
