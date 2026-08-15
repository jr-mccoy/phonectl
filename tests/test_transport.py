import json
import pytest
from droidjig.providers.transport import LoopbackTransport, next_request_id, SocketTransport


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
    monkeypatch.setattr("droidjig.providers.transport.next_request_id", lambda: rid)
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


def test_socket_transport_omits_token_when_unset():
    # Finding 2: no token configured -> the wire request carries no token field.
    conn = FakeConn([json.dumps({"ok": True, "request_id": "r", "version": 1, "data": {}}) + "\n"])
    t = SocketTransport("127.0.0.1", 8765, connect=lambda h, p, to: conn)
    t.request("ping", {}, request_id="r", timeout=1.0)
    assert "token" not in json.loads(conn.sent[-1])


def test_socket_transport_stamps_token_when_set():
    # Finding 2: a configured shared secret is attached to every request.
    conn = FakeConn([json.dumps({"ok": True, "request_id": "r", "version": 1, "data": {}}) + "\n"])
    t = SocketTransport("127.0.0.1", 8765, connect=lambda h, p, to: conn, token="s3cr3t")
    t.request("ping", {}, request_id="r", timeout=1.0)
    assert json.loads(conn.sent[-1])["token"] == "s3cr3t"


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


# ── persistent connections: the companion Server keeps conns open (30s idle) ──

def _ok_line(rid, data=None):
    return json.dumps({"ok": True, "request_id": rid, "version": 1, "data": data or {}}) + "\n"


class EchoConn(FakeConn):
    """Responds to every request line with a matching ok envelope."""
    def readline(self):
        if not self.sent:
            return ""
        rid = json.loads(self.sent[-1])["request_id"]
        return _ok_line(rid)


def _counting_factory(conns_out, conn_cls=EchoConn):
    def factory(host, port, timeout):
        c = conn_cls([])
        conns_out.append(c)
        return c
    return factory


def test_requests_reuse_one_connection():
    conns = []
    t = SocketTransport("127.0.0.1", 8765, connect=_counting_factory(conns))
    t.request("observe_native", {}, request_id="r1", timeout=1.0)
    t.request("observe_native", {}, request_id="r2", timeout=1.0)
    t.request("gesture", {}, request_id="r3", timeout=1.0)
    assert len(conns) == 1          # one TCP connection served all three
    assert not conns[0].closed


def test_idle_connection_discarded_before_server_timeout():
    clock = [100.0]
    conns = []
    t = SocketTransport("127.0.0.1", 8765, connect=_counting_factory(conns),
                        monotonic=lambda: clock[0])
    t.request("ping", {}, request_id="r1", timeout=1.0)
    clock[0] += SocketTransport.REUSE_IDLE_S + 1   # older than the reuse window
    t.request("ping", {}, request_id="r2", timeout=1.0)
    assert len(conns) == 2
    assert conns[0].closed          # preemptively dropped, never raced the server


def test_stale_cached_send_reconnects_and_resends():
    # The server closed our cached conn: the write fails, but the request line
    # never reached dispatch (no newline), so a transparent resend is safe.
    conns = []

    class DiesOnSecondUse(EchoConn):
        def sendline(self, s):
            if conns[0] is self and len(self.sent) >= 1:
                raise OSError("broken pipe")
            super().sendline(s)

    t = SocketTransport("127.0.0.1", 8765,
                        connect=_counting_factory(conns, DiesOnSecondUse))
    t.request("gesture", {}, request_id="r1", timeout=1.0)
    out = t.request("gesture", {}, request_id="r2", timeout=1.0)
    assert out["ok"] is True
    assert len(conns) == 2          # reconnected under the hood


def test_read_only_method_retries_on_dead_cached_read():
    conns = []

    class SecondConnEcho(EchoConn):
        def readline(self):
            return "" if conns[0] is self and len(self.sent) > 1 else super().readline()

    t = SocketTransport("127.0.0.1", 8765,
                        connect=_counting_factory(conns, SecondConnEcho))
    t.request("observe_native", {}, request_id="r1", timeout=1.0)
    out = t.request("observe_native", {}, request_id="r2", timeout=1.0)
    assert out["ok"] is True        # retried once on a fresh conn
    assert len(conns) == 2


def test_mutating_method_is_never_resent_on_dead_cached_read():
    # If the send may have been dispatched, a gesture must not be replayed.
    conns = []

    class SecondReadDead(EchoConn):
        def readline(self):
            return "" if conns[0] is self and len(self.sent) > 1 else super().readline()

    t = SocketTransport("127.0.0.1", 8765,
                        connect=_counting_factory(conns, SecondReadDead))
    t.request("gesture", {}, request_id="r1", timeout=1.0)
    out = t.request("gesture", {}, request_id="r2", timeout=1.0)
    assert out["ok"] is False       # surfaced as timeout, same as a lost response today
    assert len(conns) == 1          # no blind replay


# ── ping cache: capability scans must not open a socket per check ────────────

def test_ping_is_cached_within_ttl():
    conns = []
    t = SocketTransport("127.0.0.1", 8765, connect=_counting_factory(conns))
    assert t.ping() is True
    assert t.ping() is True
    assert len(conns[0].sent) == 1  # second ping answered from cache


def test_successful_request_refreshes_ping_cache():
    conns = []
    t = SocketTransport("127.0.0.1", 8765, connect=_counting_factory(conns))
    t.request("observe_native", {}, request_id="r1", timeout=1.0)
    assert t.ping() is True
    assert len(conns[0].sent) == 1  # the working RPC already proved liveness


def test_failed_connect_caches_negative_ping():
    calls = []

    def refused(host, port, timeout):
        calls.append(1)
        raise OSError("connection refused")

    t = SocketTransport("127.0.0.1", 8765, connect=refused)
    assert t.ping() is False
    assert t.ping() is False
    assert len(calls) == 1          # negative result cached for the TTL window


def test_ping_cache_expires():
    clock = [100.0]
    conns = []
    t = SocketTransport("127.0.0.1", 8765, connect=_counting_factory(conns),
                        monotonic=lambda: clock[0])
    t.ping()
    clock[0] += SocketTransport.PING_TTL + 1
    t.ping()
    assert len(conns[0].sent) == 2  # re-probed after expiry
