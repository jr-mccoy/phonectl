import json
import json as _json
import pytest
from phonectl import cli, config, errors, capabilities


def test_version_flag_prints_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip()


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.serial = "d"
        self._xml = ("""<?xml version='1.0'?><hierarchy rotation="0">"""
            """<node index="0" text="Wi-Fi" resource-id="android:id/title" class="T" """
            """content-desc="" clickable="true" bounds="[44,380][1036,520]"/></hierarchy>""")
    def get_state(self): return "device"
    def ui_dump(self): return self._xml
    def window_dump(self): return "mCurrentFocus=Window{a b com.x/.A}"
    def wm_size(self): return (1080, 2400)
    def input_tap(self, x, y): self.calls.append(("tap", x, y))
    def input_text(self, t): self.calls.append(("text", t))
    def input_key(self, k): self.calls.append(("key", k))
    def input_swipe(self, x1, y1, x2, y2, ms=200): self.calls.append(("swipe", x1, y1, x2, y2))
    def input_named_swipe(self, direction, distance_pct=0.5, ms=400): self.calls.append(("named_swipe", direction))
    def input_long_press(self, x, y, duration_ms=1000): self.calls.append(("long_press", x, y))
    def launch(self, pkg): self.calls.append(("launch", pkg))
    def screencap(self, path): return path
    def capabilities(self): return capabilities.make(
        observe_ui_tree=True, act_tap=True, act_type=True, act_key=True,
        launch_app=True, observe_screenshot=True, requires_adb=True,
    )

def test_observe_prints_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["observe"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert rc == 0
    assert data["elements"][0]["text"] == "Wi-Fi"

def test_tap_auto_mode_acts_and_logs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["tap", "--xy", "100", "200"])
    assert rc == 0
    assert ("tap", 100, 200) in fb.calls
    log = (tmp_path / "actions.jsonl").read_text()
    assert "tap" in log

def test_tap_blocked_by_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    (tmp_path / "STOP").write_text("")
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["tap", "--xy", "100", "200"])
    assert rc == 2
    assert fb.calls == []  # action refused

def test_wait_for_requires_text_or_id(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    rc = cli.main(["wait-for"])
    assert rc == 2

def test_tap_confirm_mode_refuses_without_yes(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "confirm"})
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["tap", "--xy", "1", "2"])
    assert rc == 3
    assert fb.calls == []  # confirm mode without --yes must NOT inject

def test_tap_confirm_mode_acts_with_yes(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "confirm"})
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["tap", "--xy", "1", "2", "--yes"])
    assert rc == 0
    assert ("tap", 1, 2) in fb.calls

def test_tap_dry_run_observes_but_does_not_inject(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "dry-run"})
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["tap", "--xy", "1", "2"])
    assert rc == 0
    assert fb.calls == []  # dry-run must NOT inject
    assert not (tmp_path / "actions.jsonl").exists()  # dry-run must NOT audit-log

def test_doctor_reports_connected(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "connected" in out

# Fix C: type command redacts text in audit log
def test_type_redacts_text_in_audit_log(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["type", "hunter2"])
    assert rc == 0
    assert ("text", "hunter2") in fb.calls          # real text WAS typed
    log = (tmp_path / "actions.jsonl").read_text()
    assert "hunter2" not in log                       # but NOT in the audit log
    assert "<7 chars>" in log                         # redacted surrogate present



def test_observe_json_emits_ok_envelope(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["observe", "--json"])
    out = _json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["capability"] == "ui.observe"
    assert out["provider"] == "FakeBackend"
    assert out["data"]["elements"][0]["text"] == "Wi-Fi"


def test_main_maps_phonectl_error_to_err_envelope(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    def boom(args):
        raise errors.DeviceLockedError("device is locked, unlock it")

    monkeypatch.setattr(cli, "_cmd_observe", boom)
    rc = cli.main(["observe", "--json"])
    captured = capsys.readouterr()
    out = _json.loads(captured.out)
    assert rc == 1
    assert out["ok"] is False
    assert out["error"]["code"] == "device_locked"
    assert out["error"]["requires_user"] is True
    assert "Traceback" not in captured.out


def test_doctor_json_emits_capabilities(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["doctor", "--json"])
    out = _json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["provider"] == "adb"
    assert out["data"]["connected"] is True
    assert out["data"]["capabilities"]["requires_adb"] is True


def test_tap_by_text_selector_resolves_and_logs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: b)
    rc = cli.main(["tap", "--text", "Wi-Fi", "--yes"])
    assert rc == 0
    log = (tmp_path / "actions.jsonl").read_text()
    assert "selector" in log and "Wi-Fi" in log


def test_audit_tail_prints_recent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import audit

    audit.log_action("tap", {"i": 1}, {"app": {"package": "com.x"}, "hash": "h1"})
    rc = cli.main(["audit", "tail", "--limit", "1"])
    out = capsys.readouterr().out
    assert rc == 0 and "h1" in out


def test_audit_purge_clears(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import audit

    audit.log_action("tap", {"i": 1}, {"app": {}, "hash": "h"})
    rc = cli.main(["audit", "purge"])
    assert rc == 0 and not (tmp_path / "actions.jsonl").exists()


def test_tap_json_emits_run_action_envelope(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["tap", "--xy", "1", "2", "--json"])
    out = _json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True and out["verb"] == "tap"
    assert "request_id" in out


def test_tap_busy_when_lock_held_maps_to_exit_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    from phonectl import runtime

    runtime._action_lock.acquire()
    try:
        rc = cli.main(["tap", "--xy", "1", "2", "--json"])
        out = _json.loads(capsys.readouterr().out)
    finally:
        runtime._action_lock.release()
    assert rc == 1 and out["error"]["code"] == "busy"


def test_policy_explain_reports_decision(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    class PayBackend(FakeBackend):
        def ui_dump(self):
            return (
                """<?xml version='1.0'?><hierarchy rotation="0">"""
                """<node index="0" text="Confirm payment" class="T" clickable="true" """
                """bounds="[0,0][10,10]"/></hierarchy>"""
            )

    monkeypatch.setattr(cli, "_make_backend", lambda cfg: PayBackend())
    rc = cli.main(["policy", "explain", "--text", "Confirm payment", "--json"])
    out = _json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["risk_level"] == "critical" and out["decision"] == "deny"

def test_mcp_cli_reports_missing_sdk(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import mcp_server, errors

    def boom(build=None):
        raise errors.CapabilityUnavailableError("MCP SDK not installed; pip install phonectl[mcp]")

    monkeypatch.setattr(mcp_server, "serve", boom)
    rc = cli.main(["mcp"])
    out = capsys.readouterr().out
    assert rc == 1 and "phonectl[mcp]" in out


def test_setup_verb_wires_runtime_to_run_module(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    captured = {}
    from phonectl import setup as setup_mod
    monkeypatch.setattr(setup_mod, "run_module", lambda module, conn, **kw: captured.update(module=module, conn=conn) or 0)
    rc = cli.main(["setup", "notifications"])
    assert rc == 0
    assert captured["module"] == "notifications"
    assert captured["conn"].backend.for_capability("act_tap") is fb


def test_setup_verb_defaults_to_adb(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    seen = {}
    from phonectl import setup as setup_mod
    monkeypatch.setattr(setup_mod, "run_module", lambda module, conn, **kw: seen.update(m=module) or 0)
    assert cli.main(["setup"]) == 0
    assert seen["m"] == "adb"


def test_doctor_bundle_writes_zip(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    out_zip = str(tmp_path / "diag.zip")
    from phonectl import diagnostics
    monkeypatch.setattr(diagnostics, "bundle", lambda path, backend, cfg: path)
    rc = cli.main(["doctor", "--bundle", out_zip])
    assert rc == 0
    assert out_zip in capsys.readouterr().out


# Task 3 tests — build_runtime returns ProviderRegistry

from phonectl.providers.registry import ProviderRegistry


def test_build_runtime_returns_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import config, cli
    cfg = config.load()
    backend, session, conn = cli.build_runtime(cfg)
    assert isinstance(backend, ProviderRegistry)


def test_build_runtime_wraps_explicit_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import config, cli
    cfg = config.load()
    fake = FakeBackend()
    backend, session, conn = cli.build_runtime(cfg, backend=fake)
    assert isinstance(backend, ProviderRegistry)
    assert backend.for_capability("act_tap") is fake


def test_doctor_bundle_writes_zip_when_connection_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    class OfflineBackend(FakeBackend):
        def __init__(self):
            super().__init__()
            self.serial = "stale:5555"
            self.connects = []

        def get_state(self):
            return "offline"

        def _adb(self, *args):
            self.connects.append(args)

    fb = OfflineBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    out_zip = str(tmp_path / "diag-offline.zip")
    from phonectl import diagnostics
    monkeypatch.setattr(diagnostics, "bundle", lambda path, backend, cfg: path)
    rc = cli.main(["doctor", "--bundle", out_zip])
    assert rc == 0
    assert out_zip in capsys.readouterr().out
    assert fb.connects == []


def test_clipboard_read_emits_unavailable(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["clipboard", "read", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc != 0
    assert out["ok"] is False
    assert "capability_unavailable" == out["error"]["code"]


def test_packages_list_emits_ok(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    class FakePackageBackend(FakeBackend):
        def packages_list(self, include_system=False):
            return ["com.a", "com.b"]
        def capabilities(self):
            from phonectl import capabilities
            return capabilities.make(packages_list=True, requires_adb=True,
                                     act_tap=True, observe_ui_tree=True,
                                     launch_app=True, act_type=True, act_key=True)

    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakePackageBackend())
    rc = cli.main(["packages", "list", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert "com.a" in out["data"]["packages"]


# --- Task 6: new gesture CLI verbs ---

def test_swipe_named_direction(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["swipe", "up", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True


def test_scroll_until_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["scroll-until", "--text", "NotHere", "--max", "1", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0


# ── Task 5: extraction CLI verbs ─────────────────────────────────────────────

def test_extract_list_returns_ok_envelope(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["extract", "list", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert "rows" in out["data"]


def test_find_text_regex_returns_ok(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["find", "--text-regex", "Wi.*Fi", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert "matches" in out["data"]


def test_get_focused_field_returns_ok(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["get", "focused-field", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True


# Task 5: build_runtime wires TermuxApiProvider

def test_build_runtime_includes_termux_when_available(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl.providers.termux import TermuxApiProvider

    fake_termux = TermuxApiProvider(
        which=lambda name: "/usr/bin/" + name  # always found
    )
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: fake_termux)

    cfg = config.load()
    registry, session, conn = cli.build_runtime(cfg)
    assert registry.for_capability("read_clipboard") is fake_termux


def test_build_runtime_excludes_termux_when_not_available(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: None)
    cfg = config.load()
    registry, session, conn = cli.build_runtime(cfg)
    assert registry.for_capability("read_clipboard") is None


# Task 6: device battery|wifi and tts speak CLI verbs

def test_device_battery_unavailable_without_termux(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: None)
    rc = cli.main(["device", "battery", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc != 0
    assert out["ok"] is False
    assert out["error"]["code"] == "capability_unavailable"


def test_tts_speak_unavailable_without_termux(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: None)
    rc = cli.main(["tts", "speak", "hello", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc != 0
    assert out["ok"] is False


def test_device_battery_ok_with_termux(tmp_path, monkeypatch, capsys):
    from phonectl.providers.termux import TermuxApiProvider

    battery_data = {"percentage": 42, "status": "DISCHARGING", "health": "GOOD",
                    "plugged": "UNPLUGGED", "temperature": 27.0}

    class FakeTermux(TermuxApiProvider):
        def is_available(self): return True
        def capabilities(self):
            return capabilities.make(device_battery=True, device_wifi_info=True,
                                     tts_speak=True, read_clipboard=True,
                                     write_clipboard=True)
        def battery_status(self): return battery_data

    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: FakeTermux())
    rc = cli.main(["device", "battery", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["data"]["percentage"] == 42


# --- Task 7: AccessibilityProvider wired into build_runtime ---

def test_build_runtime_prepends_accessibility_when_present(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl.providers.accessibility import AccessibilityProvider
    from phonectl.providers.transport import LoopbackTransport

    acc = AccessibilityProvider(LoopbackTransport({}))  # available
    monkeypatch.setattr(cli, "_make_accessibility_provider", lambda: acc)
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: None)
    cfg = config.load()
    registry, _, _ = cli.build_runtime(cfg)
    assert registry.for_capability("observe_ui_tree") is acc
    assert registry.for_capability("observe_ui_native") is acc


def test_build_runtime_without_accessibility_uses_adb(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_accessibility_provider", lambda: None)
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: None)
    cfg = config.load()
    registry, _, _ = cli.build_runtime(cfg)
    assert registry.for_capability("observe_ui_native") is None


def test_build_runtime_includes_notifications_when_available(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl.providers.notifications import NotificationsProvider
    from phonectl.providers.transport import LoopbackTransport
    np = NotificationsProvider(transport=LoopbackTransport({}))
    monkeypatch.setattr(cli, "_make_notifications_provider", lambda: np)
    monkeypatch.setattr(cli, "_make_accessibility_provider", lambda: None)
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: None)
    cfg = config.load()
    registry, _, _ = cli.build_runtime(cfg)
    assert registry.for_capability("observe_notifications") is np


def test_notifications_list_unavailable(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_accessibility_provider", lambda: None)
    monkeypatch.setattr(cli, "_make_notifications_provider", lambda: None)
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: None)
    rc = cli.main(["notifications", "list", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc != 0 and out["ok"] is False
    assert out["error"]["code"] == "capability_unavailable"


def test_notifications_list_ok(tmp_path, monkeypatch, capsys):
    from phonectl.providers.notifications import NotificationsProvider
    from phonectl.providers.transport import LoopbackTransport
    raw = {"key": "k", "package": "com.msg", "title": "Alice", "text": "hi",
           "category": "msg", "post_time": 1, "actions": [{"title": "Reply", "remote_input": True}]}
    np = NotificationsProvider(transport=LoopbackTransport(
        {"notifications_list": lambda p: {"notifications": [raw]}}))
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_accessibility_provider", lambda: None)
    monkeypatch.setattr(cli, "_make_notifications_provider", lambda: np)
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: None)
    rc = cli.main(["notifications", "list", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True
    assert out["data"][0]["can_reply"] is True


# --- Plan 4.3: trust status CLI ---

def test_trust_status_reports_unreachable_without_companion(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_companion_transport", lambda cfg: None)
    rc = cli.main(["trust", "status", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True
    assert out["data"]["reachable"] is False


def test_trust_status_reports_toggles(tmp_path, monkeypatch, capsys):
    from phonectl.providers.transport import LoopbackTransport
    t = LoopbackTransport({"handshake": lambda p: {
        "version": 3, "capabilities": {"act_gesture_native": True, "act_set_text_native": False},
        "stopped": False}})
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_companion_transport", lambda cfg: t)
    rc = cli.main(["trust", "status", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["data"]["version"] == 3
    assert out["data"]["capabilities"]["act_set_text_native"] is False
