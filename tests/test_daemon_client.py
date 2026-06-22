import socket

from phonectl.daemon import PROTOCOL_VERSION
from phonectl.daemon.client import DaemonClient
from phonectl import results


class FakeTransport:
    """Echoes a scripted response for each request; records sent requests."""
    def __init__(self, responder):
        self._responder = responder
        self.sent = []

    def request(self, method, params, *, request_id, timeout):
        self.sent.append((method, params))
        return self._responder(method, params, request_id)


def test_call_returns_matching_envelope():
    def responder(method, params, rid):
        return {"ok": True, "request_id": rid, "version": PROTOCOL_VERSION, "data": {"m": method}}
    c = DaemonClient("127.0.0.1", 8799, transport=FakeTransport(responder))
    out = c.call("capabilities", {})
    assert out["ok"] is True and out["data"]["m"] == "capabilities"


def test_call_unreachable_returns_daemon_unreachable():
    def responder(method, params, rid):
        raise OSError("connection refused")
    c = DaemonClient("127.0.0.1", 8799, transport=FakeTransport(responder))
    out = c.call("ping", {})
    assert out["ok"] is False and out["error"]["code"] == "daemon_unreachable"


def test_is_running_true_on_ok_ping():
    def responder(method, params, rid):
        return {"ok": True, "request_id": rid, "version": PROTOCOL_VERSION, "data": {"pong": True}}
    c = DaemonClient("127.0.0.1", 8799, transport=FakeTransport(responder))
    assert c.is_running() is True


def test_job_timeout_error_shape():
    from phonectl import errors
    e = errors.JobTimeoutError("still running")
    assert e.code == "job_timeout"
    assert e.retryable is False
    assert e.requires_user is True


def test_call_timeout_is_not_unreachable():
    def responder(method, params, rid):
        raise socket.timeout("timed out")
    c = DaemonClient("127.0.0.1", 8799, transport=FakeTransport(responder))
    out = c.call("status", {})
    assert out["ok"] is False
    assert out["error"]["code"] == "timeout"   # NOT daemon_unreachable


def test_call_connection_refused_is_unreachable():
    def responder(method, params, rid):
        raise ConnectionRefusedError("refused")
    c = DaemonClient("127.0.0.1", 8799, transport=FakeTransport(responder))
    out = c.call("ping", {})
    assert out["error"]["code"] == "daemon_unreachable"


def test_submit_and_wait_returns_inner_result_on_done():
    state = {"polls": 0}

    def responder(method, params, rid):
        if method == "act":
            return {"ok": True, "request_id": rid, "version": PROTOCOL_VERSION,
                    "data": {"job_id": "J1", "status": "accepted"}}
        if method == "job_poll":
            state["polls"] += 1
            status = "done" if state["polls"] >= 2 else "running"
            result = {"ok": True, "data": {"tapped": True}} if status == "done" else None
            return {"ok": True, "request_id": rid, "version": PROTOCOL_VERSION,
                    "data": {"status": status, "result": result}}
        raise AssertionError(method)

    c = DaemonClient("127.0.0.1", 8799, transport=FakeTransport(responder))
    out = c.submit_and_wait("act", {}, overall_timeout=5.0, poll_interval=0.0,
                            sleep=lambda s: None, now=lambda: 0.0)
    assert out["ok"] is True
    assert out["data"]["tapped"] is True


def test_submit_and_wait_caps_with_job_timeout():
    def responder(method, params, rid):
        if method == "act":
            return {"ok": True, "request_id": rid, "version": PROTOCOL_VERSION,
                    "data": {"job_id": "J9", "status": "accepted"}}
        if method == "job_poll":
            return {"ok": True, "request_id": rid, "version": PROTOCOL_VERSION,
                    "data": {"status": "running", "result": None}}
        raise AssertionError(method)

    clock = {"t": 0.0}

    def fake_now():
        clock["t"] += 1.0   # each call advances 1s
        return clock["t"]

    c = DaemonClient("127.0.0.1", 8799, transport=FakeTransport(responder))
    out = c.submit_and_wait("act", {}, overall_timeout=3.0, poll_interval=0.0,
                            sleep=lambda s: None, now=fake_now)
    assert out["ok"] is False
    assert out["error"]["code"] == "job_timeout"
    assert out["job_id"] == "J9"
