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
    config.save({"mode": "auto"})
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

def test_cli_stop_and_resume_toggle_kill_switch(tmp_path, monkeypatch):
    # Finding 1: the host CLI is the out-of-band human path for both engaging and
    # clearing the kill switch (resume is intentionally absent from agent surfaces).
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import audit
    assert cli.main(["stop"]) == 0
    assert audit.kill_switch_active() is True
    assert cli.main(["resume"]) == 0
    assert audit.kill_switch_active() is False


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
    config.save({"mode": "auto"})
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
    config.save({"mode": "auto"})
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["tap", "--xy", "1", "2", "--json"])
    out = _json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True and out["verb"] == "tap"
    assert "request_id" in out


def test_tap_busy_when_lock_held_maps_to_exit_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
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
    config.save({"mode": "auto"})
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["swipe", "up", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True


def test_scroll_until_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
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


# --- Plan 4.4: OCR provider ---

def test_ocr_screen_unavailable(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_ocr_provider", lambda: None)
    rc = cli.main(["ocr", "screen", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc != 0 and out["ok"] is False
    assert out["error"]["code"] == "capability_unavailable"


def test_ocr_screen_ok(tmp_path, monkeypatch, capsys):
    from phonectl.providers.ocr import OcrProvider

    class FakeOcr(OcrProvider):
        def __init__(self): pass
        def is_available(self): return True
        def capabilities(self):
            from phonectl import capabilities
            return capabilities.make(observe_ocr=True)
        def ocr_screen(self, registry, **kw):
            return {"regions": [{"text": "Balance", "bounds": [0, 0, 10, 10],
                                 "confidence": 0.9}], "source": "tesseract"}

    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_ocr_provider", lambda: FakeOcr())
    rc = cli.main(["ocr", "screen", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True
    assert out["data"]["regions"][0]["text"] == "Balance"


def test_find_ocr_text_returns_matching_regions(tmp_path, monkeypatch, capsys):
    from phonectl.providers.ocr import OcrProvider

    class FakeOcr(OcrProvider):
        def __init__(self): pass
        def is_available(self): return True
        def capabilities(self):
            from phonectl import capabilities
            return capabilities.make(observe_ocr=True)
        def ocr_screen(self, registry, **kw):
            return {"regions": [
                {"text": "Balance", "bounds": [0, 0, 10, 10], "confidence": 0.9},
                {"text": "Settings", "bounds": [0, 20, 10, 30], "confidence": 0.8},
            ], "source": "tesseract"}

    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_ocr_provider", lambda: FakeOcr())
    rc = cli.main(["find", "--ocr-text", "Bal.*", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True
    assert len(out["data"]["matches"]) == 1
    assert out["data"]["matches"][0]["text"] == "Balance"


# ── Task 8: _dispatch + daemon routing ────────────────────────────────────

def test_dispatch_in_process_when_no_daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: None)
    called = {"n": 0}

    def in_proc():
        called["n"] += 1
        return {"ok": True, "data": {"via": "in_process"}}

    out = cli._dispatch("observe", {}, in_proc)
    assert called["n"] == 1
    assert out["data"]["via"] == "in_process"


def test_dispatch_routes_to_daemon_when_reachable(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    class FakeClient:
        def call(self, method, params, **kw):
            return {"ok": True, "data": {"via": "daemon", "method": method}}

    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: FakeClient())
    out = cli._dispatch("observe", {}, lambda: {"ok": True, "data": {"via": "in_process"}})
    assert out["data"]["via"] == "daemon"
    assert out["data"]["method"] == "observe"


def test_observe_command_unchanged_without_daemon(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: None)
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    rc = cli.main(["observe", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and "hash" in out["data"]


# ── Task 9: daemon command ─────────────────────────────────────────────────

def test_daemon_status_reports_not_running(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: None)
    rc = cli.main(["daemon", "status", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and out["data"]["running"] is False


# ── Task 8: async routing, idempotency key, --detach, job command, shutdown RPC ──

import json as _json


class _FakeClient:
    def __init__(self, **scripted):
        self.scripted = scripted
        self.calls = []

    def submit_and_wait(self, method, params=None, *, overall_timeout,
                        poll_interval=0.5):
        self.calls.append(("submit_and_wait", method, params))
        return self.scripted["submit_and_wait"]

    def call(self, method, params=None, *, timeout=5.0):
        self.calls.append(("call", method, params))
        return self.scripted.get(method, {"ok": True, "data": {}})


def test_act_routes_through_submit_and_wait(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fake = _FakeClient(submit_and_wait={"ok": True, "data": {"tapped": True},
                                        "capability": "ui.act"})
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: fake)
    rc = cli.main(["tap", "--xy", "10", "20", "--json"])
    assert rc == 0
    assert any(c[0] == "submit_and_wait" and c[1] == "act" for c in fake.calls)
    out = _json.loads(capsys.readouterr().out)
    assert out["data"]["tapped"] is True


def test_act_params_autogenerates_idempotency_key():
    import argparse
    args = argparse.Namespace(yes=False, request_id=None, idempotency_key=None)
    p = cli._act_params(args, "tap", {"x": 1, "y": 2})
    assert isinstance(p["idempotency_key"], str) and p["idempotency_key"]
    assert isinstance(p["request_id"], str) and p["request_id"]


def test_detach_prints_job_id(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fake = _FakeClient(act={"ok": True, "data": {"job_id": "JID42", "status": "accepted"}})
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: fake)
    rc = cli.main(["tap", "--xy", "10", "20", "--detach"])
    assert rc == 0
    assert "JID42" in capsys.readouterr().out


def test_job_command_polls_and_prints(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fake = _FakeClient(job_poll={"ok": True, "data": {"status": "done",
                                "result": {"ok": True, "data": {"done": True}}}})
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: fake)
    rc = cli.main(["job", "JID42", "--json"])
    assert rc == 0
    out = _json.loads(capsys.readouterr().out)
    assert out["data"]["status"] == "done"


def test_daemon_stop_calls_shutdown_rpc(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fake = _FakeClient(shutdown={"ok": True, "data": {"stopping": True}})
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: fake)
    rc = cli.main(["daemon", "stop"])
    assert rc == 0
    assert any(c[0] == "call" and c[1] == "shutdown" for c in fake.calls)


# ── Task 9: phonectl macro CLI group ──────────────────────────────────────

def test_macro_validate_valid(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: None)
    macro_path = tmp_path / "m.json"
    macro_path.write_text(json.dumps({"name": "m", "actions": []}))
    rc = cli.main(["macro", "validate", str(macro_path), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and out["data"]["valid"] is True


def test_macro_validate_invalid(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: None)
    macro_path = tmp_path / "bad.json"
    macro_path.write_text(json.dumps({"actions": []}))
    rc = cli.main(["macro", "validate", str(macro_path), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["data"]["valid"] is False and out["data"]["errors"]


def test_macro_run_in_process(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: None)
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    macro_path = tmp_path / "m.json"
    macro_path.write_text(json.dumps({"name": "m", "actions": []}))
    rc = cli.main(["macro", "run", str(macro_path), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True


def test_macro_run_routes_through_submit_and_wait(tmp_path, monkeypatch, capsys):
    # Over the daemon, macro run submits a job and polls it: a plain call()
    # times out client-side while a long macro is still (successfully) running
    # (Finding 2, 2026-07-04).
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fake = _FakeClient(submit_and_wait={"ok": True, "capability": "macro.run",
        "data": {"run_id": "run_1", "outcome": "ok", "steps_run": 1}})
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: fake)
    macro_path = tmp_path / "m.json"
    macro_path.write_text(json.dumps(
        {"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]}))
    rc = cli.main(["macro", "run", str(macro_path), "--yes", "--json"])
    assert rc == 0
    assert any(c[0] == "submit_and_wait" and c[1] == "macro_run" for c in fake.calls)
    out = _json.loads(capsys.readouterr().out)
    assert out["data"]["run_id"] == "run_1"


def test_macro_status_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: None)
    rc = cli.main(["macro", "status", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and out["data"]["runs"] == []


# ── Task 7: macro enable / disable / list CLI ────────────────────────────────

def test_macro_enable_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: None)
    macro_path = tmp_path / "tick.json"
    macro_path.write_text(json.dumps({
        "name": "tick",
        "trigger": {"type": "schedule.interval", "every_seconds": 60},
        "actions": [{"type": "tap", "target": {"i": 0}}],
    }))
    rc = cli.main(["macro", "enable", str(macro_path), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and out["data"]["name"] == "tick"


def test_macro_list_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: None)
    macro_path = tmp_path / "tick.json"
    macro_path.write_text(json.dumps({
        "name": "tick",
        "trigger": {"type": "schedule.interval", "every_seconds": 60},
        "actions": [{"type": "tap", "target": {"i": 0}}],
    }))
    cli.main(["macro", "enable", str(macro_path)])
    capsys.readouterr()  # discard enable output
    rc = cli.main(["macro", "list", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True
    names = [m["name"] for m in out["data"]["macros"]]
    assert "tick" in names


def test_macro_disable_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: None)
    macro_path = tmp_path / "tick.json"
    macro_path.write_text(json.dumps({
        "name": "tick",
        "trigger": {"type": "schedule.interval", "every_seconds": 60},
        "actions": [{"type": "tap", "target": {"i": 0}}],
    }))
    cli.main(["macro", "enable", str(macro_path)])
    capsys.readouterr()  # discard enable output
    rc = cli.main(["macro", "disable", "tick", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and out["data"]["name"] == "tick"


def test_autonomy_grant_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: None)
    rc = cli.main(["autonomy", "grant", "reply", "--max-risk", "high", "--json"])
    assert rc == 0
    capsys.readouterr()  # drain the grant envelope
    rc = cli.main(["autonomy", "list", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert any(g["macro"] == "reply" for g in out["data"]["grants"])


def test_autonomy_grant_expires_is_seconds_from_now(tmp_path, monkeypatch, capsys):
    import time
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: None)
    before = time.time()
    rc = cli.main(["autonomy", "grant", "reply", "--max-risk", "medium",
                   "--expires", "300", "--json"])
    assert rc == 0
    granted = json.loads(capsys.readouterr().out)
    # --expires N means "N seconds from now": stored as absolute epoch, not raw N
    assert granted["data"]["expires_at"] >= before + 300
    rc = cli.main(["autonomy", "list", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert any(g["macro"] == "reply" for g in out["data"]["grants"])


def test_autonomy_grant_expires_lapses_after_duration(tmp_path, monkeypatch, capsys):
    import time
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: None)
    real_now = time.time()
    rc = cli.main(["autonomy", "grant", "reply", "--max-risk", "medium",
                   "--expires", "300", "--json"])
    assert rc == 0
    capsys.readouterr()  # drain the grant envelope
    monkeypatch.setattr(time, "time", lambda: real_now + 301)
    rc = cli.main(["autonomy", "list", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["grants"] == []


def test_autonomy_grant_expires_sent_absolute_to_daemon(tmp_path, monkeypatch):
    import time
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fake = _FakeClient()
    monkeypatch.setattr(cli, "_daemon_client", lambda cfg: fake)
    before = time.time()
    rc = cli.main(["autonomy", "grant", "reply", "--max-risk", "medium",
                   "--expires", "300", "--json"])
    assert rc == 0
    call = next(c for c in fake.calls if c[0] == "call" and c[1] == "autonomy_grant")
    assert call[2]["expires_at"] >= before + 300

# --- Companion handshake gating for notifications/OCR factories ---

def test_make_notifications_provider_gates_disabled_observe_notifications(tmp_path, monkeypatch):
    from phonectl.providers.transport import LoopbackTransport

    t = LoopbackTransport({"handshake": lambda _p: {
        "version": 1,
        "capabilities": {"observe_notifications": False, "notifications_reply": True,
                         "notifications_dismiss": True},
        "stopped": False,
    }})
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_companion_transport", lambda cfg: t)
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: None)

    p = cli._make_notifications_provider()

    assert p is not None
    assert p.capabilities()["observe_notifications"] is False


def test_make_notifications_provider_gates_notifications_reply_and_dismiss(tmp_path, monkeypatch):
    from phonectl.providers.transport import LoopbackTransport

    t = LoopbackTransport({"handshake": lambda _p: {
        "version": 1,
        "capabilities": {"observe_notifications": True, "notifications_reply": False,
                         "notifications_dismiss": False},
        "stopped": False,
    }})
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_companion_transport", lambda cfg: t)
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: None)

    p = cli._make_notifications_provider()
    caps = p.capabilities()

    assert caps["observe_notifications"] is True
    assert caps["notifications_reply"] is False
    assert caps["notifications_dismiss"] is False


def test_make_notifications_provider_preserves_termux_only_observe(tmp_path, monkeypatch):
    class FakeTermux:
        def is_available(self): return True

    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_companion_transport", lambda cfg: None)
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: FakeTermux())

    p = cli._make_notifications_provider()
    caps = p.capabilities()

    assert caps["observe_notifications"] is True
    assert caps["notifications_reply"] is False
    assert caps["notifications_dismiss"] is False


def test_make_ocr_provider_gates_companion_observe_ocr(tmp_path, monkeypatch):
    from phonectl.providers.ocr import OcrProvider
    from phonectl.providers.transport import LoopbackTransport

    t = LoopbackTransport({"handshake": lambda _p: {
        "version": 1,
        "capabilities": {"observe_ocr": False},
        "stopped": False,
    }})
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_companion_transport", lambda cfg: t)
    monkeypatch.setattr(cli, "OcrProvider", lambda transport=None: OcrProvider(
        which=lambda _name: None, transport=transport))

    p = cli._make_ocr_provider()

    assert p is not None
    assert p.capabilities()["observe_ocr"] is False


def test_make_ocr_provider_preserves_local_tesseract_when_companion_disabled(tmp_path, monkeypatch):
    from phonectl.providers.ocr import OcrProvider
    from phonectl.providers.transport import LoopbackTransport

    t = LoopbackTransport({"handshake": lambda _p: {
        "version": 1,
        "capabilities": {"observe_ocr": False},
        "stopped": False,
    }})
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_companion_transport", lambda cfg: t)
    monkeypatch.setattr(cli, "OcrProvider", lambda transport=None: OcrProvider(
        which=lambda _name: "/usr/bin/tesseract", transport=transport))

    p = cli._make_ocr_provider()

    assert p is not None
    assert p.capabilities()["observe_ocr"] is True


def test_default_mode_requires_confirmation(tmp_path, monkeypatch, capsys):
    # Finding 5: with no config at all, actions must not run unconfirmed.
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    fb = FakeBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["tap", "--xy", "100", "200"])
    assert rc == 3
    assert fb.calls == []
    rc = cli.main(["tap", "--xy", "100", "200", "--yes"])
    assert rc == 0
    assert ("tap", 100, 200) in fb.calls


# ── Finding 15: uniform exit codes across command handlers ────────────────────

class _IntentClipBackend(FakeBackend):
    def capabilities(self):
        return capabilities.make(
            observe_ui_tree=True, act_tap=True, act_type=True, act_key=True,
            launch_app=True, observe_screenshot=True, requires_adb=True,
            intent_start=True, intent_broadcast=True, write_clipboard=True,
        )

    def intent_start(self, **kw):
        self.calls.append(("intent_start", kw))

    def intent_broadcast(self, action, **kw):
        self.calls.append(("intent_broadcast", action))

    def clipboard_write(self, text):
        self.calls.append(("clipboard_write", text))


def test_intent_start_stopped_exits_2(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    (tmp_path / "STOP").write_text("")
    fb = _IntentClipBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["intent", "start", "--action", "android.intent.action.VIEW", "--yes"])
    assert rc == 2
    assert fb.calls == []


def test_clipboard_write_confirm_required_exits_3(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))  # default mode = confirm
    fb = _IntentClipBackend()
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: fb)
    rc = cli.main(["clipboard", "write", "hello"])
    assert rc == 3
    assert fb.calls == []


def test_cli_config_set_and_get(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import cli
    assert cli.main(["config", "set", "companion_port", "8765"]) == 0
    assert cli.main(["config", "get", "companion_port"]) == 0
    assert "8765" in capsys.readouterr().out


def test_cli_config_set_unknown_key_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import cli
    assert cli.main(["config", "set", "not_a_real_key", "x"]) == 2


# ── Task 12: companion setup/status CLI wiring ────────────────────────────────

def test_cli_companion_setup_dispatches(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    apk = tmp_path / "app-debug.apk"; apk.write_bytes(b"X")
    from phonectl import cli, companion_setup

    class _Backend:  # stands in for AdbBackend
        serial = "1.2.3.4:5"
        def run_adb(self, *a):
            import subprocess; return subprocess.CompletedProcess(a, 0, stdout="", stderr="")
    class _Conn:
        def __init__(self): self.backend = _Backend()
        def ensure(self): pass
    monkeypatch.setattr(cli, "build_runtime", lambda cfg: (_Conn().backend, None, _Conn()))
    monkeypatch.setattr(companion_setup, "run_companion_setup",
                        lambda adb, cfg, **k: {"ok": True, "steps": [
                            companion_setup.step("install", "done", "ok")]})
    rc = cli.main(["companion", "setup", "--apk", str(apk), "--yes"])
    assert rc == 0
    assert "install" in capsys.readouterr().out


def test_cli_companion_status_dispatches(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    import subprocess
    from phonectl import cli, companion_setup
    config.save({"companion_token": "t"})

    def fake_run_adb(*a):
        if a[:3] == ("shell", "pm", "list"):
            return subprocess.CompletedProcess(a, 0, stdout="package:com.phonectl.companion", stderr="")
        if a[:4] == ("shell", "settings", "get", "secure"):
            return subprocess.CompletedProcess(a, 0, stdout=companion_setup.ACCESSIBILITY_COMPONENT, stderr="")
        if a[:2] == ("shell", "ss"):
            return subprocess.CompletedProcess(a, 0, stdout="LISTEN 0 0 [::ffff:127.0.0.1]:8765 *:*", stderr="")
        return subprocess.CompletedProcess(a, 0, stdout="", stderr="")

    class _Backend:  # stands in for AdbBackend
        serial = "1.2.3.4:5"
        def run_adb(self, *a):
            return fake_run_adb(*a)
    class _Conn:
        def ensure(self): pass
    monkeypatch.setattr(cli, "build_runtime", lambda cfg: (_Backend(), None, _Conn()))
    rc = cli.main(["companion", "status", "--json"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {"installed": True, "accessibility": True, "socket": True, "token_paired": True}


def test_exit_codes_uniform_across_commands():
    # Finding 15: every command maps its result envelope to an exit code through the single
    # _exit_code helper — ok -> 0, stopped -> 2, confirmation_required -> 3, any other error -> 1.
    # No command invents its own codes.
    assert cli._exit_code({"ok": True}) == 0
    assert cli._exit_code({"ok": True, "data": {}}) == 0
    assert cli._exit_code({"ok": False, "error": {"code": "stopped"}}) == 2
    assert cli._exit_code({"ok": False, "error": {"code": "confirmation_required"}}) == 3
    for code in ("guarded_action", "rate_limited", "stale_snapshot", "observe_failed",
                 "capability_unavailable", "bad_request", "anything_else"):
        assert cli._exit_code({"ok": False, "error": {"code": code}}) == 1
    # A malformed error envelope (no code) still resolves to the generic failure code, never crashes.
    assert cli._exit_code({"ok": False}) == 1
    assert cli._exit_code({"ok": False, "error": {}}) == 1


def _parser_returning(func, *, json_flag):
    """A stand-in parser whose parsed args run `func` — lets the tests drive
    main()'s error handling without depending on any real subcommand."""
    import argparse

    class _P:
        def parse_args(self, argv):
            return argparse.Namespace(func=func, json=json_flag)

        def print_help(self):
            pass

    return lambda: _P()


# ── Unexpected-error handling (audit D2) ───────────────────────────────────
# errors.py promises envelopes "without raw tracebacks", but main() caught only
# PhonectlError, so anything else — a bug, an OSError, a corrupt state file —
# escaped as a traceback and bypassed the whole structured-result contract.

def _boom(args):
    raise RuntimeError("something unexpected went wrong")


def test_unexpected_error_prints_a_message_not_a_traceback(monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_parser", _parser_returning(_boom, json_flag=False))
    rc = cli.main(["observe"])
    out = capsys.readouterr()
    assert rc == 4, "unexpected internal errors get their own exit code"
    assert "Traceback" not in out.out + out.err
    assert "something unexpected went wrong" in out.out
    assert "internal error" in out.out.lower()


def test_unexpected_error_is_reported_as_a_result_envelope_under_json(monkeypatch, capsys):
    import json as _json
    monkeypatch.setattr(cli, "build_parser", _parser_returning(_boom, json_flag=True))
    rc = cli.main(["observe"])
    env = _json.loads(capsys.readouterr().out)
    assert rc == 4
    assert env["ok"] is False
    assert env["error"]["code"] == "internal_error"
    assert env["error"]["user_action"], "must tell the user what to do (file an issue)"


def test_unexpected_error_reraises_under_debug(monkeypatch):
    # The traceback stays available for developers, just off the default path.
    monkeypatch.setattr(cli, "build_parser", _parser_returning(_boom, json_flag=False))
    monkeypatch.setenv("PHONECTL_DEBUG", "1")
    with pytest.raises(RuntimeError):
        cli.main(["observe"])


def test_keyboard_interrupt_is_not_swallowed_as_an_internal_error(monkeypatch, capsys):
    # Ctrl-C is a user action, not a bug: it must not print "please file an issue".
    def interrupt(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "build_parser", _parser_returning(interrupt, json_flag=False))
    rc = cli.main(["observe"])
    out = capsys.readouterr().out
    assert rc == 130, "128 + SIGINT, the shell convention"
    assert "internal error" not in out.lower()


def test_broken_pipe_exits_quietly(monkeypatch, capsys):
    # `phonectl observe --json | head` closes the pipe; that is not an error.
    def broken(args):
        raise BrokenPipeError

    monkeypatch.setattr(cli, "build_parser", _parser_returning(broken, json_flag=False))
    assert cli.main(["observe"]) == 0
    assert "internal error" not in capsys.readouterr().out.lower()
