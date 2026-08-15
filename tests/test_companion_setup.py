from droidjig import companion_setup as cs
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
                    cp(out="package:com.droidjig.companion"))])
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
            return cp(out="package:com.droidjig.companion")
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
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    adb = FakeAdb([(lambda a: "run-as" in a, cp(out=XML_OK))])
    cfg = {}
    r = cs.acquire_token(adb, cfg, (lambda m: None), prompt=(lambda m="": "SHOULD_NOT_BE_USED"))
    assert r["status"] == "done" and cfg["companion_token"] == "abc123"
    from droidjig import config
    assert config.load()["companion_token"] == "abc123"

def test_acquire_token_prompt_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    adb = FakeAdb([(lambda a: "run-as" in a, cp(rc=1, err="not debuggable"))])
    cfg = {}
    r = cs.acquire_token(adb, cfg, (lambda m: None), prompt=(lambda m="": "  pasted-tok  "))
    assert r["status"] == "done" and cfg["companion_token"] == "pasted-tok"
    assert any(a[:3] == ("shell", "am", "start") for a in adb.calls)  # app launched for the user


def _listening(port=8765):
    return cp(out=f"LISTEN 0 0 [::ffff:127.0.0.1]:{port} *:*")

def test_start_server_skips_when_already_up(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    adb = FakeAdb([(lambda a: a[:2] == ("shell", "ss"), _listening())])
    r = cs.start_server(adb, "tok", {}, (lambda m: None), assume_yes=True,
                        prompt=(lambda m="": "n"), sleep=(lambda s: None))
    assert r["status"] == "skipped"
    assert not any(a[:3] == ("shell", "am", "broadcast") for a in adb.calls)

def test_start_server_declined_without_yes(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    adb = FakeAdb([(lambda a: a[:2] == ("shell", "ss"), cp(out=""))])
    r = cs.start_server(adb, "tok", {}, (lambda m: None), assume_yes=False,
                        prompt=(lambda m="": "n"), sleep=(lambda s: None))
    assert r["status"] == "failed" and not r["ok"]
    assert not any(a[:3] == ("shell", "am", "broadcast") for a in adb.calls)

def test_start_server_broadcasts_then_up(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
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
    from droidjig import config
    assert config.load()["companion_port"] == cs.DEFAULT_PORT
    assert config.load()["companion_host"] == "127.0.0.1"

def test_start_server_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    adb = FakeAdb([(lambda a: a[:2] == ("shell", "ss"), cp(out=""))])
    r = cs.start_server(adb, "tok", {}, (lambda m: None), assume_yes=True,
                        prompt=(lambda m="": "n"), sleep=(lambda s: None), attempts=3)
    assert r["status"] == "failed" and not r["ok"]

class _HS:
    def __init__(self, reachable, stopped, caps):
        self.reachable, self.stopped, self.capabilities = reachable, stopped, caps

def test_verify_reachable_reports_caps():
    seen = {}
    def fake_factory(host, port, *, token=None):
        seen.update(host=host, port=port, token=token); return object()
    r = cs.verify({"companion_token": "t", "companion_port": 8765},
                  negotiate=lambda t, **k: _HS(True, False, {"observe_ui_native": True}),
                  transport_factory=fake_factory)
    assert r["status"] == "done" and r["data"]["reachable"] is True
    assert seen["token"] == "t" and seen["port"] == 8765

def test_verify_unreachable_fails():
    r = cs.verify({"companion_token": "t"},
                  negotiate=lambda t, **k: _HS(False, False, {}),
                  transport_factory=lambda *a, **k: object())
    assert r["status"] == "failed" and not r["ok"]

def test_verify_none_port_falls_back_to_default():
    seen = {}
    def fake_factory(host, port, *, token=None):
        seen["port"] = port
        return object()
    cfg = {"companion_token": "t", "companion_port": None}  # as config.load() seeds it
    r = cs.verify(cfg, negotiate=lambda t, **k: _HS(True, False, {}),
                  transport_factory=fake_factory)
    assert r["status"] == "done" and seen["port"] == cs.DEFAULT_PORT

def test_start_server_skip_persists_port(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    adb = FakeAdb([(lambda a: a[:2] == ("shell", "ss"), _listening())])
    cfg = {}
    r = cs.start_server(adb, "tok", cfg, (lambda m: None), assume_yes=True,
                        prompt=(lambda m="": "n"), sleep=(lambda s: None))
    assert r["status"] == "skipped"
    assert cfg["companion_port"] == cs.DEFAULT_PORT


def test_orchestrator_runs_all_steps_happy(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    apk = tmp_path / "app-debug.apk"; apk.write_bytes(b"X")
    def adb(*a):
        if a[:3] == ("shell", "pm", "list"): return cp(out="")
        if a[0] == "install": return cp(out="Success")
        if a[:4] == ("shell", "settings", "get", "secure"): return cp(out="null")
        if a[:3] == ("shell", "dumpsys", "package"): return cp(out="POST_NOTIFICATIONS: granted=true")
        if "run-as" in a: return cp(out=XML_OK)
        if a[:2] == ("shell", "ss"): return _listening()
        return cp(out="")
    res = cs.run_companion_setup(
        adb, {}, apk_path=str(apk), assume_yes=True,
        prompt=(lambda m="": "n"), out=(lambda m: None), sleep=(lambda s: None),
        verify_kwargs={"negotiate": lambda t, **k: _HS(True, False, {"observe_ui_native": True}),
                       "transport_factory": lambda *a, **k: object()})
    assert res["ok"] is True
    assert [s["name"] for s in res["steps"]] == \
        ["install", "accessibility", "notifications", "token", "server", "verify"]

def test_orchestrator_stops_on_failed_step(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    apk = tmp_path / "app-debug.apk"; apk.write_bytes(b"X")
    def adb(*a):
        if a[:3] == ("shell", "pm", "list"): return cp(out="")
        if a[0] == "install": return cp(rc=1, err="INSTALL_FAILED")  # fail at step 1
        return cp(out="")
    res = cs.run_companion_setup(adb, {}, apk_path=str(apk), assume_yes=True,
                                 prompt=(lambda m="": "n"), out=(lambda m: None), sleep=(lambda s: None))
    assert res["ok"] is False and res["steps"][-1]["name"] == "install"
    assert len(res["steps"]) == 1  # stopped, did not proceed


def test_status_reports_state():
    adb = FakeAdb([
        (lambda a: a[:3] == ("shell", "pm", "list"), cp(out="package:com.droidjig.companion")),
        (lambda a: a[:4] == ("shell", "settings", "get", "secure"), cp(out=cs.ACCESSIBILITY_COMPONENT)),
        (lambda a: a[:2] == ("shell", "ss"), cp(out="LISTEN 0 0 [::ffff:127.0.0.1]:8765 *:*")),
    ])
    rep = cs.status(adb, {"companion_token": "t"})
    assert rep == {"installed": True, "accessibility": True, "socket": True, "token_paired": True}


def test_push_token_mints_and_broadcasts(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    adb = FakeAdb([])
    cfg = {}
    out_lines = []
    res = cs.push_token(adb, cfg, out_lines.append, mint=lambda: "deadbeef")
    assert res["status"] == "done"
    assert cfg["companion_token"] == "deadbeef"
    # A SET_TOKEN broadcast carrying the minted token was sent to the LifecycleReceiver.
    bcast = [c for c in adb.calls if "broadcast" in c and cs.SET_TOKEN_ACTION in c]
    assert bcast, adb.calls
    assert cs.LIFECYCLE_COMPONENT in bcast[0]
    assert "deadbeef" in " ".join(bcast[0])


def test_push_token_skips_when_already_paired(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    adb = FakeAdb([])
    cfg = {"companion_token": "existing"}
    res = cs.push_token(adb, cfg, lambda _m: None, mint=lambda: "new")
    assert res["status"] == "skipped"
    assert cfg["companion_token"] == "existing"
    assert adb.calls == []  # no broadcast when a token already exists


def test_mint_token_is_32_hex_chars():
    t = cs._mint_token()
    assert len(t) == 32 and all(c in "0123456789abcdef" for c in t)
