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
    cmd = calls[0][0]
    assert "monkey" in cmd and "com.android.settings" in cmd
