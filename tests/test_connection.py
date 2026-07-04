import pytest
from phonectl.connection import Connection, GUIDANCE
from phonectl import capabilities as caps_mod
from phonectl import config
from phonectl.providers.registry import ProviderRegistry


class ScanBackend:
    """Fake backend: get_state() reports 'device' only once connected to the target addr."""
    def __init__(self, open_ports, target):
        self.serial = None
        self._open = list(open_ports)
        self._target = target
        self.adb_calls = []
    def get_state(self):
        return "device" if self.serial == self._target else "offline"
    def _adb(self, *args):
        self.adb_calls.append(args)
        return ""
    def scan_ports(self, ip, ports, *, timeout=0.3, workers=200):
        return sorted(self._open)

class StateBackend:
    def __init__(self, states):
        self.serial = None
        self._states = list(states)
        self.adb_calls = []
    def get_state(self):
        return self._states.pop(0) if len(self._states) > 1 else self._states[0]
    def _adb(self, *args):
        self.adb_calls.append(args)
        return ""

class AdbProvider(StateBackend):
    """An ADB-capable provider, as held inside a ProviderRegistry."""
    def capabilities(self):
        return caps_mod.make(requires_adb=True)

def test_ensure_noop_when_already_device(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = StateBackend(["device"])
    Connection(b, {"serial": "127.0.0.1:5555"}).ensure()
    assert b.adb_calls == []  # no reconnect attempted

def test_ensure_reconnects_when_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = StateBackend(["offline", "device"])
    Connection(b, {"serial": "127.0.0.1:5555"}).ensure()
    assert ("connect", "127.0.0.1:5555") in b.adb_calls
    assert b.serial == "127.0.0.1:5555"

def test_ensure_raises_guidance_when_no_serial(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = StateBackend(["offline", "offline"])
    with pytest.raises(ConnectionError) as e:
        Connection(b, {}).ensure()
    assert GUIDANCE in str(e.value)

def test_ensure_raises_guidance_when_reconnect_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = StateBackend(["offline", "offline"])
    with pytest.raises(ConnectionError) as e:
        Connection(b, {"serial": "127.0.0.1:5555"}).ensure()
    # a reconnect WAS attempted using the configured serial...
    assert ("connect", "127.0.0.1:5555") in b.adb_calls
    # ...but state never reached "device", so guidance is raised
    assert GUIDANCE in str(e.value)

def test_ensure_reconnects_through_provider_registry(tmp_path, monkeypatch):
    # build_runtime wraps the backend in a ProviderRegistry, whose `serial` is a
    # read-only property. The reconnect path assigns backend.serial, which must not crash.
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    p = AdbProvider(["offline", "device"])
    registry = ProviderRegistry([p])
    Connection(registry, {"serial": "127.0.0.1:5555"}).ensure()
    assert ("connect", "127.0.0.1:5555") in p.adb_calls
    assert p.serial == "127.0.0.1:5555"
    assert registry.serial == "127.0.0.1:5555"

def test_rediscover_finds_live_port_via_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = ScanBackend(open_ports=[43091], target="192.168.0.109:43091")
    cfg = {"serial": "192.168.0.109:44063", "last_port": "192.168.0.109:44063"}
    addr = Connection(b, cfg).rediscover()
    assert addr == "192.168.0.109:43091"
    assert b.serial == "192.168.0.109:43091"
    assert config.load()["serial"] == "192.168.0.109:43091"

def test_rediscover_skips_open_non_device_ports(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    # 8765 (companion) is open but never 'device'; 43091 is the real adbd port
    b = ScanBackend(open_ports=[8765, 43091], target="192.168.0.109:43091")
    cfg = {"serial": "192.168.0.109:44063", "last_port": "192.168.0.109:44063"}
    addr = Connection(b, cfg).rediscover()
    assert addr == "192.168.0.109:43091"

def test_ensure_auto_recovers_via_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = ScanBackend(open_ports=[43091], target="192.168.0.109:43091")
    cfg = {"serial": "192.168.0.109:44063", "last_port": "192.168.0.109:44063"}
    Connection(b, cfg).ensure()  # must not raise
    assert b.serial == "192.168.0.109:43091"
    assert config.load()["serial"] == "192.168.0.109:43091"

def test_ensure_raises_guidance_when_scan_finds_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = ScanBackend(open_ports=[], target="192.168.0.109:43091")
    cfg = {"serial": "192.168.0.109:44063", "last_port": "192.168.0.109:44063"}
    with pytest.raises(ConnectionError) as e:
        Connection(b, cfg).ensure()
    assert GUIDANCE in str(e.value)
