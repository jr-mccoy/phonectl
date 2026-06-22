import json as _json
import pytest
from phonectl.providers.termux import TermuxApiProvider
from phonectl import capabilities


def _fake_which_found(name):
    return "/data/data/com.termux/files/usr/bin/" + name


def _fake_which_missing(name):
    return None


class FakeRunner:
    def __init__(self, outputs):
        self.outputs = outputs  # list of stdout strings, consumed in order
        self.calls = []

    def __call__(self, cmd, *, capture_output=True, text=True, input=None, **kw):
        self.calls.append(cmd)
        stdout = self.outputs.pop(0) if self.outputs else ""
        return type("R", (), {"stdout": stdout, "returncode": 0})()


# Task 1: discovery and capabilities

def test_is_available_true_when_on_path():
    p = TermuxApiProvider(which=_fake_which_found)
    assert p.is_available() is True


def test_is_available_false_when_not_on_path():
    p = TermuxApiProvider(which=_fake_which_missing)
    assert p.is_available() is False


def test_capabilities_all_true_when_available():
    p = TermuxApiProvider(which=_fake_which_found)
    caps = p.capabilities()
    assert caps["read_clipboard"] is True
    assert caps["write_clipboard"] is True
    assert caps["device_battery"] is True
    assert caps["device_wifi_info"] is True
    assert caps["tts_speak"] is True


def test_capabilities_all_false_when_not_available():
    p = TermuxApiProvider(which=_fake_which_missing)
    caps = p.capabilities()
    assert all(v is False for v in caps.values())


# Task 2: clipboard read/write

def test_clipboard_read_calls_termux_clipboard_get():
    runner = FakeRunner(["hello world\n"])
    p = TermuxApiProvider(runner=runner, which=_fake_which_found)
    result = p.clipboard_read()
    assert result == "hello world"
    assert any("termux-clipboard-get" in str(c) for c in runner.calls)


def test_clipboard_write_passes_text_via_stdin():
    calls = []

    def fake_runner(cmd, *, capture_output=True, text=True, input=None, **kw):
        calls.append((cmd, input))
        return type("R", (), {"stdout": "", "returncode": 0})()

    p = TermuxApiProvider(runner=fake_runner, which=_fake_which_found)
    p.clipboard_write("test text")
    assert any("termux-clipboard-set" in str(c[0]) for c in calls)
    assert any(c[1] == "test text" for c in calls)


def test_clipboard_read_strips_trailing_newline():
    runner = FakeRunner(["clipboard content\n"])
    p = TermuxApiProvider(runner=runner, which=_fake_which_found)
    assert p.clipboard_read() == "clipboard content"


# Task 3: battery + wifi

_BATTERY_JSON = _json.dumps({
    "health": "GOOD", "percentage": 87,
    "plugged": "UNPLUGGED", "status": "DISCHARGING", "temperature": 28.5
})

_WIFI_JSON = _json.dumps({
    "bssid": "aa:bb:cc:dd:ee:ff", "frequency_mhz": 5180, "ip": "192.168.1.5",
    "link_speed_mbps": 433, "mac_address": "11:22:33:44:55:66",
    "rssi": -45, "ssid": "HomeNet"
})


def test_battery_status_parses_json():
    runner = FakeRunner([_BATTERY_JSON])
    p = TermuxApiProvider(runner=runner, which=_fake_which_found)
    bat = p.battery_status()
    assert bat["percentage"] == 87
    assert bat["status"] == "DISCHARGING"


def test_battery_status_calls_termux_battery_status():
    runner = FakeRunner([_BATTERY_JSON])
    p = TermuxApiProvider(runner=runner, which=_fake_which_found)
    p.battery_status()
    assert any("termux-battery-status" in str(c) for c in runner.calls)


def test_wifi_info_parses_json():
    runner = FakeRunner([_WIFI_JSON])
    p = TermuxApiProvider(runner=runner, which=_fake_which_found)
    info = p.wifi_info()
    assert info["ssid"] == "HomeNet"
    assert info["ip"] == "192.168.1.5"


def test_wifi_info_returns_disconnected_on_empty_output():
    runner = FakeRunner([""])
    p = TermuxApiProvider(runner=runner, which=_fake_which_found)
    info = p.wifi_info()
    assert info.get("connected") is False or info.get("ssid") is None


# Task 4: TTS

def test_tts_speak_calls_termux_tts_speak():
    runner = FakeRunner([""])
    p = TermuxApiProvider(runner=runner, which=_fake_which_found)
    p.tts_speak("hello world")
    cmd = " ".join(str(a) for a in runner.calls[-1])
    assert "termux-tts-speak" in cmd
    assert "hello world" in cmd


def test_tts_speak_includes_language_flag():
    runner = FakeRunner([""])
    p = TermuxApiProvider(runner=runner, which=_fake_which_found)
    p.tts_speak("bonjour", language="fr")
    cmd = " ".join(str(a) for a in runner.calls[-1])
    assert "-l" in cmd and "fr" in cmd


def test_tts_speak_includes_rate_flag():
    runner = FakeRunner([""])
    p = TermuxApiProvider(runner=runner, which=_fake_which_found)
    p.tts_speak("fast speech", rate=1.5)
    cmd = " ".join(str(a) for a in runner.calls[-1])
    assert "-r" in cmd and "1.5" in cmd
