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
