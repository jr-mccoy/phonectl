import pytest
from phonectl.providers.transport import LoopbackTransport, next_request_id


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
