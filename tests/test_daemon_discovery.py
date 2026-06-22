import json

import pytest

from phonectl import errors
from phonectl.daemon import discovery


def test_daemon_unreachable_and_unknown_method_codes():
    assert errors.DaemonUnreachableError().code == "daemon_unreachable"
    assert errors.UnknownMethodError().code == "unknown_method"


def test_write_read_remove_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    info = {"pid": 4321, "host": "127.0.0.1", "port": 8799, "version": 1, "started_at": 1.0}
    path = discovery.write(info)
    assert path.exists()
    assert discovery.read()["port"] == 8799
    discovery.remove()
    assert discovery.read() is None
    discovery.remove()  # idempotent


def test_write_rejects_non_loopback(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    with pytest.raises(ValueError):
        discovery.write({"pid": 1, "host": "10.0.0.5", "port": 8799, "version": 1, "started_at": 0.0})


def test_read_corrupt_file_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    (tmp_path / "daemon.json").write_text("{not json")
    assert discovery.read() is None


def test_discover_reachable_calls_ping(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    discovery.write({"pid": 1, "host": "127.0.0.1", "port": 8799, "version": 1, "started_at": 0.0})
    seen = {}

    def fake_ping(host, port):
        seen["called"] = (host, port)
        return True

    assert discovery.discover(ping=fake_ping)["port"] == 8799
    assert seen["called"] == ("127.0.0.1", 8799)


def test_discover_stale_file_failing_ping_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    discovery.write({"pid": 1, "host": "127.0.0.1", "port": 8799, "version": 1, "started_at": 0.0})
    assert discovery.discover(ping=lambda h, p: False) is None
    assert discovery.read() is not None  # not removed, just ignored


def test_discover_no_file_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    assert discovery.discover(ping=lambda h, p: True) is None
