import pytest
from phonectl.adb_backend import AdbBackend

class FakeCompleted:
    def __init__(self, stdout="", stdout_bytes=b"", returncode=0):
        self.stdout = stdout
        self._bytes = stdout_bytes
        self.returncode = returncode

def make_runner(record, stdout="", stdout_bytes=b""):
    def runner(cmd, **kwargs):
        record.append((cmd, kwargs))
        if kwargs.get("capture_output") and not kwargs.get("text", False):
            return FakeCompleted(stdout_bytes=stdout_bytes)
        return FakeCompleted(stdout=stdout)
    return runner

def test_adb_prepends_serial():
    calls = []
    b = AdbBackend(serial="127.0.0.1:5555", runner=make_runner(calls, stdout="ok"))
    out = b._adb("shell", "echo", "ok")
    assert out == "ok"
    assert calls[0][0] == ["adb", "-s", "127.0.0.1:5555", "shell", "echo", "ok"]

def test_wm_size_parses():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout="Physical size: 1080x2400\n"))
    assert b.wm_size() == (1080, 2400)


def test_wm_size_is_cached_within_ttl():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout="Physical size: 1080x2400\n"))
    assert b.wm_size() == (1080, 2400)
    assert b.wm_size() == (1080, 2400)
    assert len(calls) == 1   # second call served from cache, no adb round trip


def test_wm_size_cache_invalidated_on_serial_change():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout="Physical size: 1080x2400\n"))
    b.wm_size()
    b.serial = "127.0.0.1:5555"   # reconnect to a (possibly different) device
    b.wm_size()
    assert len(calls) == 2


def test_wm_size_cache_expires_after_ttl(monkeypatch):
    import phonectl.adb_backend as mod
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout="Physical size: 1080x2400\n"))
    t = [1000.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: t[0])
    b.wm_size()
    t[0] += b.WM_SIZE_TTL + 1
    b.wm_size()
    assert len(calls) == 2


def test_wm_size_ttl_zero_disables_cache():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout="Physical size: 1080x2400\n"),
                   wm_size_ttl=0)
    b.wm_size()
    b.wm_size()
    assert len(calls) == 2

def test_input_tap_builds_command():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls))
    b.input_tap(540, 450)
    assert calls[0][0] == ["adb", "-s", "d", "shell", "input", "tap", "540", "450"]

def test_launch_uses_monkey():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls))
    b.launch("com.android.settings")
    assert calls[0][0] == ["adb", "-s", "d", "shell", "monkey", "-p",
                           "com.android.settings", "-c",
                           "android.intent.category.LAUNCHER", "1"]

def test_adb_no_serial_omits_s_flag():
    calls = []
    b = AdbBackend(serial=None, runner=make_runner(calls, stdout="device"))
    b.get_state()
    assert calls[0][0] == ["adb", "get-state"]

def test_screencap_writes_bytes_and_returns_path(tmp_path):
    calls = []
    png = b"\x89PNG\r\n\x1a\nFAKEDATA"
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout_bytes=png))
    dest = str(tmp_path / "snap.png")
    out = b.screencap(dest)
    assert out == dest
    assert (tmp_path / "snap.png").read_bytes() == png
    assert calls[0][0] == ["adb", "-s", "d", "exec-out", "screencap", "-p"]

# Fix A: window_dump uses "dumpsys window" (not "dumpsys window windows")
def test_window_dump_builds_correct_command():
    calls = []
    sample = "mCurrentFocus=Window{abc123 u0 com.android.settings/.Settings}\n"
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout=sample))
    out = b.window_dump()
    assert calls[0][0] == ["adb", "-s", "d", "shell", "dumpsys", "window"]
    assert "mCurrentFocus" in out
    assert out == sample

# Fix B: input_text shell-quotes metacharacters via shlex.quote
def test_input_text_shell_quotes_metacharacters():
    import shlex
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls))
    b.input_text("a b$c")
    expected_quoted = shlex.quote("a b$c")
    assert calls[0][0] == ["adb", "-s", "d", "shell", "input", "text", expected_quoted]


def test_wake_sends_wakeup_keyevent():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls))
    b.wake()
    assert calls[0][0] == ["adb", "-s", "d", "shell", "input", "keyevent", "WAKEUP"]


def test_keyguard_true_when_window_dump_shows_lockscreen():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout="  mDreamingLockscreen=true\n"))
    assert b.keyguard() is True
    assert calls[0][0] == ["adb", "-s", "d", "shell", "dumpsys", "window"]


def test_lock_state_reports_structured_state():
    b = AdbBackend(serial="d", runner=make_runner([], stdout="KeyguardServiceDelegate{showing=true secure=true}"))
    ls = b.lock_state()
    assert ls["lock_state"] == "locked_secure"
    assert ls["can_act"] is False


def test_mdns_services_runs_adb_and_parses():
    calls = []
    out = "List of discovered mdns services\nadb-1\t_adb-tls-connect._tcp\t10.0.0.5:43210\n"
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout=out))
    assert b.mdns_services() == ["10.0.0.5:43210"]
    assert calls[0][0] == ["adb", "-s", "d", "mdns", "services"]


def test_adb_version_runs_version():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout="Android Debug Bridge version 1.0.41\n"))
    assert "1.0.41" in b.adb_version()
    assert calls[0][0] == ["adb", "-s", "d", "version"]


def test_devices_runs_devices_l():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout="List of devices attached\n127.0.0.1:41000 device\n"))
    out = b.devices()
    assert "127.0.0.1:41000" in out
    assert calls[0][0] == ["adb", "-s", "d", "devices", "-l"]


def test_adb_capabilities_include_new_keys():
    calls = []
    caps = AdbBackend(serial="d", runner=make_runner(calls)).capabilities()
    assert caps["write_clipboard"] is True
    assert caps["packages_list"] is True
    assert caps["packages_stop"] is True
    assert caps["packages_clear"] is True
    assert caps["intent_start"] is True
    assert caps["intent_broadcast"] is True
    assert caps["read_clipboard"] is False


def test_clipboard_write_calls_service_call():
    calls = []
    AdbBackend(serial=None, runner=make_runner(calls)).clipboard_write("hello world")
    assert any("service" in str(c[0]) and "clipboard" in str(c[0]) for c in calls)


def test_clipboard_read_calls_service_call():
    calls = []
    AdbBackend(serial=None, runner=make_runner(calls)).clipboard_read()
    assert any("service" in str(c[0]) and "clipboard" in str(c[0]) for c in calls)


def test_intent_start_builds_correct_command():
    calls = []
    AdbBackend(serial=None, runner=make_runner(calls)).intent_start(
        action="android.intent.action.VIEW",
        data="geo:0,0",
        extras={"q": "coffee"},
    )
    cmd = " ".join(str(a) for a in calls[-1][0])
    assert "am" in cmd and "start" in cmd
    assert "android.intent.action.VIEW" in cmd
    assert "geo:0,0" in cmd
    assert "--es" in cmd and "q" in cmd and "coffee" in cmd


def test_intent_broadcast_builds_correct_command():
    calls = []
    AdbBackend(serial=None, runner=make_runner(calls)).intent_broadcast(
        "com.example.ACTION", extras={"key": "val"}
    )
    cmd = " ".join(str(a) for a in calls[-1][0])
    assert "broadcast" in cmd and "com.example.ACTION" in cmd
    assert "--es" in cmd and "val" in cmd


def test_packages_list_strips_prefix():
    out = "package:com.example.a\npackage:com.example.b\n"
    pkgs = AdbBackend(serial=None, runner=make_runner([], stdout=out)).packages_list()
    assert pkgs == ["com.example.a", "com.example.b"]


def test_packages_list_user_only_excludes_system():
    calls = []
    AdbBackend(serial=None, runner=make_runner(calls)).packages_list(include_system=False)
    cmd = " ".join(str(a) for a in calls[-1][0])
    assert "-3" in cmd


def test_packages_stop_calls_force_stop():
    calls = []
    AdbBackend(serial=None, runner=make_runner(calls)).packages_stop("com.foo")
    cmd = " ".join(str(a) for a in calls[-1][0])
    assert "force-stop" in cmd and "com.foo" in cmd


def test_packages_clear_calls_pm_clear():
    calls = []
    AdbBackend(serial=None, runner=make_runner(calls)).packages_clear("com.foo")
    cmd = " ".join(str(a) for a in calls[-1][0])
    assert "pm" in cmd and "clear" in cmd and "com.foo" in cmd


# --- Task 1: Named swipe directions ---

@pytest.fixture
def calls():
    class CallsRecorder:
        def __init__(self):
            self.recorded = []
        def __call__(self, cmd, **kwargs):
            self.recorded.append(cmd)
            return FakeCompleted()
    return CallsRecorder()


@pytest.fixture
def wm_size_runner():
    def make(w, h, calls_recorder):
        size_out = f"Physical size: {w}x{h}\n"
        first = [True]
        def runner(cmd, **kwargs):
            if first[0]:
                first[0] = False
                return FakeCompleted(stdout=size_out)
            calls_recorder.recorded.append(cmd)
            return FakeCompleted()
        return runner
    return make


def test_input_named_swipe_up_calls_swipe_with_correct_direction(calls, wm_size_runner):
    AdbBackend(serial=None, runner=wm_size_runner(1080, 2400, calls)).input_named_swipe("up")
    cmd = " ".join(str(a) for a in calls.recorded[-1])
    assert "input" in cmd and "swipe" in cmd


def test_input_named_swipe_unknown_direction_raises():
    with pytest.raises(ValueError, match="unknown swipe direction"):
        AdbBackend(serial=None).input_named_swipe("diagonal")


# --- Task 2: Long-press ---

def test_input_long_press_issues_zero_distance_swipe(calls):
    AdbBackend(serial=None, runner=calls).input_long_press(300, 500, 1500)
    cmd = " ".join(str(a) for a in calls.recorded[-1])
    assert "swipe" in cmd
    assert cmd.count("300") >= 2 and cmd.count("500") >= 2
    assert "1500" in cmd


# --- Task 3: Fling ---

def test_input_fling_issues_fast_swipe(calls, wm_size_runner):
    AdbBackend(serial=None, runner=wm_size_runner(1080, 2400, calls)).input_fling("up")
    cmd = " ".join(str(a) for a in calls.recorded[-1])
    assert "swipe" in cmd


def test_input_fling_unknown_direction_raises():
    with pytest.raises(ValueError):
        AdbBackend(serial=None).input_fling("sideways")


# Finding 7: intent/launch args reach the device shell and are re-tokenized
# there — every value must be shlex-quoted like input_text/clipboard_write.
@pytest.mark.parametrize("hostile", [
    "a b", "a;reboot", "x&y", "$(id)", "`id`", "it's", 'say "hi"',
])
def test_intent_fields_shell_quoted(hostile):
    import shlex
    calls = []
    AdbBackend(serial=None, runner=make_runner(calls)).intent_start(
        action=hostile, data=hostile, component=hostile, extras={hostile: hostile},
    )
    cmd = calls[-1][0]
    assert hostile not in cmd  # raw value must never appear as a bare token
    assert cmd.count(shlex.quote(hostile)) >= 4


@pytest.mark.parametrize("hostile", ["a b", "a;reboot", "$(id)"])
def test_intent_broadcast_fields_shell_quoted(hostile):
    import shlex
    calls = []
    AdbBackend(serial=None, runner=make_runner(calls)).intent_broadcast(
        hostile, extras={hostile: hostile}
    )
    cmd = calls[-1][0]
    assert hostile not in cmd
    assert cmd.count(shlex.quote(hostile)) >= 3


def test_launch_package_shell_quoted():
    import shlex
    calls = []
    AdbBackend(serial=None, runner=make_runner(calls)).launch("com.x; reboot")
    cmd = calls[-1][0]
    assert "com.x; reboot" not in cmd
    assert shlex.quote("com.x; reboot") in cmd


# Task 2: run_adb full-result seam
def test_run_adb_returns_full_result_with_serial():
    calls = []
    def fake_runner(cmd, **kw):
        calls.append(cmd)
        import subprocess
        return subprocess.CompletedProcess(cmd, 7, stdout="OUT", stderr="ERR")
    from phonectl.adb_backend import AdbBackend
    b = AdbBackend(serial="1.2.3.4:5", runner=fake_runner)
    res = b.run_adb("shell", "true")
    assert calls == [["adb", "-s", "1.2.3.4:5", "shell", "true"]]
    assert (res.returncode, res.stdout, res.stderr) == (7, "OUT", "ERR")


def test_scan_ports_returns_sorted_open_ports():
    opened = {43091, 8765}
    def fake_probe(ip, port, timeout):
        assert ip == "192.168.0.109"
        return port in opened
    b = AdbBackend(port_probe=fake_probe)
    result = b.scan_ports("192.168.0.109", [50000, 8765, 40000, 43091])
    assert result == [8765, 43091]


def test_scan_ports_empty_when_none_open():
    b = AdbBackend(port_probe=lambda ip, port, timeout: False)
    assert b.scan_ports("192.168.0.109", [1, 2, 3]) == []


def test_scan_ports_empty_input():
    b = AdbBackend(port_probe=lambda ip, port, timeout: True)
    assert b.scan_ports("192.168.0.109", []) == []


def test_scan_ports_default_probe_finds_real_listener():
    # Exercises the real selectors-based scan path (no injected probe) against a
    # live loopback listener; a just-released port is treated as closed.
    import socket as _socket
    srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    open_port = srv.getsockname()[1]
    tmp = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    tmp.bind(("127.0.0.1", 0))
    closed_port = tmp.getsockname()[1]
    tmp.close()
    try:
        b = AdbBackend()  # default socket probe -> selectors path
        found = b.scan_ports("127.0.0.1", [open_port, closed_port], timeout=0.5)
        assert open_port in found
        assert closed_port not in found
    finally:
        srv.close()


# ── observe_dump: UI hierarchy + window state in ONE adb round trip ──────────

_OD_XML = "<?xml version='1.0'?><hierarchy rotation=\"0\"></hierarchy>"
_OD_WINDOW = "  mCurrentFocus=Window{a b com.android.settings/.Settings}\n"


def _od_stdout(xml=_OD_XML, window=_OD_WINDOW):
    return xml + "\n" + AdbBackend.OBSERVE_SEP + "\n" + window


def test_observe_dump_is_one_adb_call_with_filtered_window():
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout=_od_stdout()))
    xml, window = b.observe_dump()
    assert len(calls) == 1
    cmd = calls[0][0]
    assert cmd[:4] == ["adb", "-s", "d", "exec-out"]
    shell_cmd = cmd[4]
    assert "uiautomator dump /dev/tty" in shell_cmd
    assert "dumpsys window" in shell_cmd
    assert "grep -E" in shell_cmd          # filtered device-side, not shipped whole
    assert "<hierarchy" in xml
    assert "mCurrentFocus" in window


def test_observe_dump_filter_covers_every_parser_pattern():
    # The device-side grep must keep every line ui_parser/observer read:
    # focus, keyguard flags, delegate line, and secure markers.
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout=_od_stdout()))
    b.observe_dump()
    shell_cmd = calls[0][0][4]
    for pat in ("mCurrentFocus", "mFocusedApp", "mDreamingLockscreen",
                "mShowingLockscreen", "KeyguardServiceDelegate", "secure="):
        assert pat in shell_cmd


def test_observe_dump_missing_separator_falls_back_to_none_window():
    # A shell that ignored the compound command returns the plain dump; the
    # caller must fetch the window separately rather than mis-split.
    calls = []
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout=_OD_XML))
    xml, window = b.observe_dump()
    assert "<hierarchy" in xml
    assert window is None


def test_observe_dump_junk_window_section_returns_none():
    # grep missing on the device -> error text instead of window lines. Never
    # hand that to parse_lock_state (it would read as "unlocked").
    calls = []
    out = _od_stdout(window="grep: not found\n")
    b = AdbBackend(serial="d", runner=make_runner(calls, stdout=out))
    xml, window = b.observe_dump()
    assert "<hierarchy" in xml
    assert window is None


def test_observe_dump_keeps_lock_lines_for_parser():
    from phonectl import ui_parser
    locked = ("  mDreamingLockscreen=true\n"
              "  KeyguardServiceDelegate showing=true secure=true\n")
    b = AdbBackend(serial="d", runner=make_runner([], stdout=_od_stdout(window=locked)))
    _xml, window = b.observe_dump()
    ls = ui_parser.parse_lock_state(window)
    assert ls["lock_state"] == "locked_secure"
    assert ls["can_act"] is False
