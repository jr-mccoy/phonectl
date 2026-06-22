from phonectl.daemon import PROTOCOL_VERSION
from phonectl.daemon.client import DaemonClient


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
