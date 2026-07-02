from phonectl import companion_setup as cs
import subprocess

XML_OK = ('<?xml version="1.0"?><map>'
          '<string name="companion_token">abc123</string>'
          '<boolean name="cap_observe_ui_native" value="true"/></map>')


class FakeAdb:
    """Maps a matcher predicate over args -> CompletedProcess; records calls."""
    def __init__(self, rules):  # rules: list[(predicate, CompletedProcess)]
        self.rules = rules; self.calls = []
    def __call__(self, *args):
        self.calls.append(args)
        for pred, res in self.rules:
            if pred(args):
                return res
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

def cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, stdout=out, stderr=err)

def test_parse_token_extracts_value():
    assert cs.parse_token(XML_OK) == "abc123"

def test_parse_token_missing_returns_none():
    assert cs.parse_token('<map><string name="stopped">x</string></map>') is None

def test_parse_token_blank_returns_none():
    assert cs.parse_token('<map><string name="companion_token"></string></map>') is None

def test_parse_token_garbage_returns_none():
    assert cs.parse_token("run-as: package not debuggable") is None

def test_step_shape():
    assert cs.step("verify", "done", "ok") == {
        "name": "verify", "ok": True, "status": "done", "message": "ok"}

def test_ensure_installed_installs_when_absent(tmp_path):
    apk = tmp_path / "app-debug.apk"; apk.write_bytes(b"APKBYTES")
    adb = FakeAdb([
        (lambda a: a[:3] == ("shell", "pm", "list"), cp(out="")),        # not listed
        (lambda a: a[0] == "install", cp(out="Success")),
    ])
    cfg = {}; out = []
    r = cs.ensure_installed(adb, str(apk), cfg, out.append)
    assert r["status"] == "done" and r["ok"]
    assert any(a[0] == "install" for a in adb.calls)
    import hashlib
    assert cfg["companion_apk_sha"] == hashlib.sha256(b"APKBYTES").hexdigest()

def test_ensure_installed_skips_when_sha_matches(tmp_path):
    apk = tmp_path / "app-debug.apk"; apk.write_bytes(b"APKBYTES")
    import hashlib
    cfg = {"companion_apk_sha": hashlib.sha256(b"APKBYTES").hexdigest()}
    adb = FakeAdb([(lambda a: a[:3] == ("shell", "pm", "list"),
                    cp(out="package:com.phonectl.companion"))])
    r = cs.ensure_installed(adb, str(apk), cfg, (lambda m: None))
    assert r["status"] == "skipped"
    assert not any(a[0] == "install" for a in adb.calls)

def test_ensure_installed_signature_mismatch_reinstalls(tmp_path):
    apk = tmp_path / "app-debug.apk"; apk.write_bytes(b"APKBYTES")
    seq = [cp(rc=0, out="Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: signatures do not match]"),
           cp(out="Success")]
    calls = []
    installs = []
    def adb(*a):
        calls.append(a)
        if a[:3] == ("shell", "pm", "list"):
            return cp(out="package:com.phonectl.companion")
        if a[0] == "install":
            installs.append(a); return seq.pop(0) if len(seq) > 1 else seq[0]
        if a[0] == "uninstall":
            return cp(out="Success")
        return cp(out="")
    cfg = {}
    r = cs.ensure_installed(adb, str(apk), cfg, (lambda m: None))
    assert r["status"] == "done" and r["ok"]
    assert any(c[:2] == ("uninstall", cs.PACKAGE) for c in calls)
    assert installs[0][:2] == ("install", "-r")
    assert installs[1][0] == "install" and "-r" not in installs[1]
    import hashlib
    assert cfg["companion_apk_sha"] == hashlib.sha256(b"APKBYTES").hexdigest()

def test_ensure_accessibility_skips_when_already_enabled():
    adb = FakeAdb([(lambda a: a[:4] == ("shell", "settings", "get", "secure"),
                    cp(out=cs.ACCESSIBILITY_COMPONENT))])
    r = cs.ensure_accessibility(adb, (lambda m: None), assume_yes=True, prompt=(lambda m="": "y"))
    assert r["status"] == "skipped"
    assert not any(a[2] == "put" for a in adb.calls if len(a) > 2)

def test_ensure_accessibility_appends_when_yes():
    adb = FakeAdb([(lambda a: a[:4] == ("shell", "settings", "get", "secure"), cp(out="null"))])
    r = cs.ensure_accessibility(adb, (lambda m: None), assume_yes=True, prompt=(lambda m="": "n"))
    assert r["status"] == "done"
    puts = [a for a in adb.calls if len(a) > 2 and a[2] == "put"]
    assert any(cs.ACCESSIBILITY_COMPONENT in a for a in puts)
    assert any(a[-2:] == ("accessibility_enabled", "1") for a in puts)

def test_ensure_accessibility_declined_without_yes():
    adb = FakeAdb([(lambda a: a[:4] == ("shell", "settings", "get", "secure"), cp(out="null"))])
    r = cs.ensure_accessibility(adb, (lambda m: None), assume_yes=False, prompt=(lambda m="": "n"))
    assert r["status"] == "failed" and not r["ok"]
    assert not any(len(a) > 2 and a[2] == "put" for a in adb.calls)

def test_ensure_accessibility_appends_to_existing_service():
    existing = "com.other/OtherService"
    adb = FakeAdb([(lambda a: a[:4] == ("shell", "settings", "get", "secure"), cp(out=existing))])
    r = cs.ensure_accessibility(adb, (lambda m: None), assume_yes=True, prompt=(lambda m="": "n"))
    assert r["status"] == "done"
    puts = [a for a in adb.calls
            if len(a) > 5 and a[2] == "put" and a[4] == "enabled_accessibility_services"]
    assert puts, "expected a put to enabled_accessibility_services"
    assert puts[-1][-1] == existing + ":" + cs.ACCESSIBILITY_COMPONENT  # existing entry preserved

def test_ensure_notifications_grants_when_missing():
    adb = FakeAdb([(lambda a: a[:3] == ("shell", "dumpsys", "package"),
                    cp(out="android.permission.POST_NOTIFICATIONS: granted=false"))])
    out = []
    r = cs.ensure_notifications(adb, out.append)
    assert r["status"] == "done"
    assert any(a[:3] == ("shell", "pm", "grant") for a in adb.calls)
    assert any("notification access" in m.lower() for m in out)

def test_ensure_notifications_skips_when_granted():
    adb = FakeAdb([(lambda a: a[:3] == ("shell", "dumpsys", "package"),
                    cp(out="android.permission.POST_NOTIFICATIONS: granted=true"))])
    r = cs.ensure_notifications(adb, (lambda m: None))
    assert r["status"] == "skipped"
    assert not any(a[:3] == ("shell", "pm", "grant") for a in adb.calls)

def test_read_token_via_runas_success():
    adb = FakeAdb([(lambda a: "run-as" in a, cp(out=XML_OK))])
    assert cs.read_token_via_runas(adb) == "abc123"

def test_read_token_via_runas_denied_returns_none():
    adb = FakeAdb([(lambda a: "run-as" in a, cp(rc=1, err="run-as: not debuggable"))])
    assert cs.read_token_via_runas(adb) is None

def test_acquire_token_runas(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    adb = FakeAdb([(lambda a: "run-as" in a, cp(out=XML_OK))])
    cfg = {}
    r = cs.acquire_token(adb, cfg, (lambda m: None), prompt=(lambda m="": "SHOULD_NOT_BE_USED"))
    assert r["status"] == "done" and cfg["companion_token"] == "abc123"
    from phonectl import config
    assert config.load()["companion_token"] == "abc123"

def test_acquire_token_prompt_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    adb = FakeAdb([(lambda a: "run-as" in a, cp(rc=1, err="not debuggable"))])
    cfg = {}
    r = cs.acquire_token(adb, cfg, (lambda m: None), prompt=(lambda m="": "  pasted-tok  "))
    assert r["status"] == "done" and cfg["companion_token"] == "pasted-tok"
    assert any(a[:3] == ("shell", "am", "start") for a in adb.calls)  # app launched for the user


def _listening(port=8765):
    return cp(out=f"LISTEN 0 0 [::ffff:127.0.0.1]:{port} *:*")

def test_start_server_skips_when_already_up(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    adb = FakeAdb([(lambda a: a[:2] == ("shell", "ss"), _listening())])
    r = cs.start_server(adb, "tok", {}, (lambda m: None), assume_yes=True,
                        prompt=(lambda m="": "n"), sleep=(lambda s: None))
    assert r["status"] == "skipped"
    assert not any(a[:3] == ("shell", "am", "broadcast") for a in adb.calls)

def test_start_server_declined_without_yes(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    adb = FakeAdb([(lambda a: a[:2] == ("shell", "ss"), cp(out=""))])
    r = cs.start_server(adb, "tok", {}, (lambda m: None), assume_yes=False,
                        prompt=(lambda m="": "n"), sleep=(lambda s: None))
    assert r["status"] == "failed" and not r["ok"]
    assert not any(a[:3] == ("shell", "am", "broadcast") for a in adb.calls)

def test_start_server_broadcasts_then_up(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    seq = [cp(out=""), _listening()]  # down, then up after broadcast
    calls = []
    def ss_dispatch(*a):
        calls.append(a)
        if a[:2] == ("shell", "ss"):
            return seq.pop(0) if len(seq) > 1 else seq[0]
        return cp(out="")
    cfg = {}
    r = cs.start_server(ss_dispatch, "tok", cfg, (lambda m: None), assume_yes=True,
                        prompt=(lambda m="": "n"), sleep=(lambda s: None))
    assert r["status"] == "done"
    assert any(a[:3] == ("shell", "am", "broadcast") and "tok" in a and cs.LIFECYCLE_COMPONENT in a
               for a in calls)
    assert cfg["companion_port"] == cs.DEFAULT_PORT
    from phonectl import config
    assert config.load()["companion_port"] == cs.DEFAULT_PORT
    assert config.load()["companion_host"] == "127.0.0.1"

def test_start_server_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    adb = FakeAdb([(lambda a: a[:2] == ("shell", "ss"), cp(out=""))])
    r = cs.start_server(adb, "tok", {}, (lambda m: None), assume_yes=True,
                        prompt=(lambda m="": "n"), sleep=(lambda s: None), attempts=3)
    assert r["status"] == "failed" and not r["ok"]
