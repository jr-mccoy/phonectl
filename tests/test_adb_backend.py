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
