import pytest
from phonectl import capabilities, setup


class RecordingConn:
    def __init__(self, states, cfg=None):
        self.cfg = cfg if cfg is not None else {}
        self.pairs = []
        self.connects = []
        self.backend = _RecordingBackend(states)

    def pair(self, addr, code):
        self.pairs.append((addr, code))

    def connect(self, addr):
        self.connects.append(addr)
        self.backend.serial = addr
        self.cfg["serial"] = addr


class _RecordingBackend:
    def __init__(self, states):
        self.serial = None
        self._states = list(states)

    def get_state(self):
        return self._states.pop(0) if len(self._states) > 1 else self._states[0]


def scripted(answers):
    it = iter(answers)
    return lambda _msg="": next(it)


def collector():
    lines = []
    return lines, lambda msg="": lines.append(str(msg))


def test_happy_path_pairs_connects_and_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    conn = RecordingConn(states=["offline", "device"])
    out_lines, out = collector()
    rc = setup.run_setup(
        conn,
        prompt=scripted(["127.0.0.1:37000", "482913", "127.0.0.1:41000"]),
        out=out,
        which=lambda name: "/usr/bin/adb",
        exists=lambda path: True,
    )
    assert rc == 0
    assert conn.pairs == [("127.0.0.1:37000", "482913")]
    assert conn.connects == ["127.0.0.1:41000"]
    assert conn.cfg["serial"] == "127.0.0.1:41000"
    assert conn.cfg["mode"] == "confirm"   # safe default (Finding 5); auto is opt-in
    assert conn.cfg["last_port"] == "41000"
    from phonectl import config
    saved = config.load()
    assert saved["serial"] == "127.0.0.1:41000"
    assert saved["last_port"] == "41000"
    assert any("connected" in line.lower() for line in out_lines)


def test_missing_adb_prints_termux_guidance_and_does_not_pair(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    conn = RecordingConn(states=["device"])
    out_lines, out = collector()
    rc = setup.run_setup(conn, prompt=scripted([]), out=out, which=lambda name: None, exists=lambda path: True)
    assert rc == 1
    assert conn.pairs == [] and conn.connects == []
    joined = "\n".join(out_lines)
    assert "pkg install android-tools" in joined
    assert "installing" not in joined.lower()


def test_verify_failure_returns_2_and_does_not_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    conn = RecordingConn(states=["offline"])
    out_lines, out = collector()
    rc = setup.run_setup(
        conn,
        prompt=scripted(["127.0.0.1:37000", "482913", "127.0.0.1:41000"]),
        out=out,
        which=lambda name: "/usr/bin/adb",
        exists=lambda path: True,
    )
    assert rc == 2
    assert conn.connects == ["127.0.0.1:41000"]
    assert not (tmp_path / "config.json").exists()
    assert any("did not come online" in line for line in out_lines)


def test_adbkey_absent_emits_note_and_does_not_fabricate(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    conn = RecordingConn(states=["offline", "device"])
    out_lines, out = collector()
    checked = []

    def fake_exists(path):
        checked.append(path)
        return False

    rc = setup.run_setup(
        conn,
        prompt=scripted(["127.0.0.1:37000", "482913", "127.0.0.1:41000"]),
        out=out,
        which=lambda name: "/usr/bin/adb",
        exists=fake_exists,
    )
    assert rc == 0
    assert any("first server start" in line for line in out_lines)
    assert any(p.endswith("adbkey") for p in checked)


def test_already_connected_fast_path_skips_pairing(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    conn = RecordingConn(states=["device"], cfg={"serial": "127.0.0.1:41000"})
    conn.backend.serial = "127.0.0.1:41000"
    out_lines, out = collector()

    def no_prompt(_msg=""):
        raise AssertionError("fast-path must not prompt the user")

    rc = setup.run_setup(conn, prompt=no_prompt, out=out, which=lambda name: "/usr/bin/adb", exists=lambda path: True)
    assert rc == 0
    assert conn.pairs == [] and conn.connects == []
    assert any("already connected" in line.lower() for line in out_lines)


def test_rediscover_branch_used_when_available(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    class RediscoverConn(RecordingConn):
        def __init__(self):
            super().__init__(states=["offline", "device"], cfg={"serial": "127.0.0.1:41000"})
            self.rediscovers = 0

        def rediscover(self):
            self.rediscovers += 1
            self.backend.serial = "127.0.0.1:41000"
            return "127.0.0.1:41000"

    conn = RediscoverConn()
    rc = setup.run_setup(conn, prompt=scripted(["y"]), out=collector()[1], which=lambda name: "/usr/bin/adb", exists=lambda path: True)
    assert rc == 0
    assert conn.rediscovers == 1
    assert conn.pairs == [] and conn.connects == []


def test_module_report_adb_available():
    caps = capabilities.make(requires_adb=True, act_tap=True, observe_ui_tree=True)
    rep = setup.module_report("adb", caps=caps, which=lambda n: "/usr/bin/adb")
    assert rep["available"] is True
    assert "capabilities_unlocked" in rep and rep["how_to_enable"]


def test_module_report_accessibility_unavailable_with_guidance():
    caps = capabilities.make(requires_adb=True)
    rep = setup.module_report("accessibility", caps=caps)
    assert rep["available"] is False
    assert "Accessibility" in rep["how_to_enable"]
    assert rep["safety"]


def test_module_report_termux_api_uses_which():
    caps = capabilities.make(requires_adb=True)
    yes = setup.module_report("termux-api", caps=caps, which=lambda n: "/data/.../termux-battery")
    no = setup.module_report("termux-api", caps=caps, which=lambda n: None)
    assert yes["available"] is True and no["available"] is False


def test_module_report_unknown_raises():
    with pytest.raises(ValueError):
        setup.module_report("teleport", caps=capabilities.make())


def test_run_module_reports_without_pairing(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    class CapConn(RecordingConn):
        def __init__(self):
            super().__init__(states=["device"], cfg={})
            self.backend.capabilities = lambda: capabilities.make(requires_adb=True)

    conn = CapConn()
    out_lines, out = collector()
    rc = setup.run_module("notifications", conn, prompt=lambda _m="": (_ for _ in ()).throw(AssertionError("no prompt")), out=out, which=lambda n: None, exists=lambda p: True)
    assert rc == 0
    assert conn.pairs == [] and conn.connects == []
    assert any("notification" in line.lower() for line in out_lines)


def test_rediscover_error_falls_back_to_pairing(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    class RediscoverFailsConn(RecordingConn):
        def __init__(self):
            super().__init__(states=["offline", "offline", "device"], cfg={"serial": "old:5555"})
            self.rediscovers = 0

        def rediscover(self):
            self.rediscovers += 1
            raise ConnectionError("no device")

    conn = RediscoverFailsConn()
    out_lines, out = collector()
    rc = setup.run_setup(
        conn,
        prompt=scripted(["y", "127.0.0.1:37000", "482913", "127.0.0.1:41000"]),
        out=out,
        which=lambda name: "/usr/bin/adb",
        exists=lambda path: True,
    )
    assert rc == 0
    assert conn.rediscovers == 1
    assert conn.pairs == [("127.0.0.1:37000", "482913")]
    assert conn.connects == ["127.0.0.1:41000"]
    assert any("falling back" in line for line in out_lines)


def test_verify_failure_with_real_connection_does_not_save_failed_serial(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl.connection import Connection

    class OfflineBackend:
        def __init__(self):
            self.serial = None
            self.calls = []

        def _adb(self, *args):
            self.calls.append(args)

        def get_state(self):
            return "offline"

    backend = OfflineBackend()
    conn = Connection(backend, {})
    rc = setup.run_setup(
        conn,
        prompt=scripted(["127.0.0.1:37000", "482913", "127.0.0.1:41000"]),
        out=collector()[1],
        which=lambda name: "/usr/bin/adb",
        exists=lambda path: True,
    )
    assert rc == 2
    assert backend.calls == [("pair", "127.0.0.1:37000", "482913"), ("connect", "127.0.0.1:41000")]
    assert conn.cfg == {}
    assert not (tmp_path / "config.json").exists()
