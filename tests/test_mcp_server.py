import builtins
import inspect

import pytest

from phonectl import audit, capabilities as caps, cli, errors, mcp_server
from phonectl.config import config_dir


class FakeConn:
    def ensure(self):
        pass


class FakeBackend:
    serial = "d"

    def __init__(self):
        self.taps = []
        self._xml = (
            """<?xml version='1.0'?><hierarchy rotation="0">"""
            """<node index="0" text="Wi-Fi" resource-id="android:id/title" class="T" """
            """content-desc="" clickable="true" bounds="[44,380][1036,520]"/></hierarchy>"""
        )

    def get_state(self):
        return "device"

    def ui_dump(self):
        return self._xml

    def window_dump(self):
        return "mCurrentFocus=Window{a b com.x/.A}"

    def wm_size(self):
        return (1080, 2400)

    def input_tap(self, x, y):
        self.taps.append((x, y))

    def input_text(self, t):
        self.taps.append(("text", t))

    def input_swipe(self, x1, y1, x2, y2, ms):
        self.taps.append(("swipe", x1, y1, x2, y2, ms))

    def input_named_swipe(self, direction, distance_pct=0.5, ms=400):
        self.taps.append(("named_swipe", direction))

    def input_long_press(self, x, y, duration_ms=1000):
        self.taps.append(("long_press", x, y))

    def input_key(self, keycode):
        self.taps.append(("key", keycode))

    def launch(self, package):
        self.taps.append(("launch", package))

    def capabilities(self):
        return caps.make(observe_ui_tree=True, act_tap=True, requires_adb=True)


def make_build(backend=None):
    from phonectl.session import Session

    backend = backend or FakeBackend()

    def build(cfg):
        return backend, Session(), FakeConn()

    return build, backend


def test_observe_ui_returns_ok_envelope():
    build, _ = make_build()
    env = mcp_server.observe_ui(build)
    assert env["ok"] is True
    assert env["capability"] == "ui.observe"
    assert env["data"]["elements"][0]["text"] == "Wi-Fi"


def test_find_returns_candidates_and_confidence():
    build, _ = make_build()
    env = mcp_server.find(build, selector={"text": "Wi-Fi"})
    assert env["ok"] is True
    assert env["data"]["candidates"][0]["text"] == "Wi-Fi"
    assert env["data"]["confidence"] == 1.0


def test_find_empty_match_is_zero_confidence():
    build, _ = make_build()
    env = mcp_server.find(build, selector={"text": "Nope"})
    assert env["data"]["candidates"] == [] and env["data"]["confidence"] == 0.0


def test_capabilities_tool_describes_backend():
    build, _ = make_build()
    env = mcp_server.capabilities(build)
    assert env["ok"] is True
    assert env["data"]["capabilities"]["requires_adb"] is True
    assert "observe_ui_tree" in env["data"]["summary"]


def test_tap_by_selector_routes_through_run_action(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    build, backend = make_build()
    env = mcp_server.tap(build, selector={"text": "Wi-Fi"}, confirm=True)
    assert env["ok"] is True and env["verb"] == "tap"
    assert backend.taps and backend.taps[0] == (540, 450)
    log = (tmp_path / "actions.jsonl").read_text()
    assert "selector" in log


def test_tap_dry_run_does_not_act(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    build, backend = make_build()
    env = mcp_server.tap(build, index=0, dry_run=True)
    assert env["ok"] is True and env["dry_run"] is True
    assert backend.taps == []
    assert not (tmp_path / "actions.jsonl").exists()


def test_tap_kill_switch_returns_stopped(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    (tmp_path / "STOP").write_text("")
    build, backend = make_build()
    env = mcp_server.tap(build, index=0, confirm=True)
    assert env["ok"] is False and env["error"]["code"] == "stopped"
    assert backend.taps == []


def test_type_text_routes_and_audits_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    build, backend = make_build()
    env = mcp_server.type_text(build, text="hunter2", confirm=True)
    assert env["ok"] is True
    assert ("text", "hunter2") in backend.taps
    log = (tmp_path / "actions.jsonl").read_text()
    assert "hunter2" not in log


def test_policy_explain_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    class PayBackend(FakeBackend):
        def ui_dump(self):
            return (
                """<?xml version='1.0'?><hierarchy rotation="0">"""
                """<node index="0" text="Confirm payment" class="T" clickable="true" """
                """bounds="[0,0][10,10]"/></hierarchy>"""
            )

    build, _ = make_build(PayBackend())
    env = mcp_server.policy_explain(build, verb="tap", index=0)
    assert env["ok"] is True
    assert env["data"]["risk_level"] == "critical"
    assert env["data"]["decision"] == "deny"


def test_audit_query_returns_recent_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    audit.log_action("tap", {"i": 1}, {"app": {"package": "com.x"}, "hash": "h1"})
    env = mcp_server.audit_query(limit=5)
    assert env["ok"] is True
    assert env["data"]["entries"][-1]["hash"] == "h1"


def test_stop_and_resume_toggle_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    assert mcp_server.stop()["data"]["stopped"] is True
    assert (config_dir() / "STOP").exists()
    assert mcp_server.resume()["data"]["stopped"] is False
    assert not (config_dir() / "STOP").exists()


def test_registry_lists_stable_tool_names():
    for name in (
        "phone_observe_ui", "phone_find", "phone_capabilities", "phone_tap",
        "phone_type", "phone_swipe", "phone_key", "phone_launch",
        "phone_policy_explain", "phone_audit_query", "phone_stop", "phone_resume",
    ):
        assert name in mcp_server.TOOLS
        assert callable(mcp_server.TOOLS[name]["handler"])
        assert mcp_server.TOOLS[name]["schema"]["type"] == "object"


def test_call_tool_dispatches_observe(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    build, _ = make_build()
    env = mcp_server.call_tool("phone_observe_ui", {}, build=build)
    assert env["ok"] is True and env["capability"] == "ui.observe"


def test_call_tool_dispatches_buildless_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    audit.log_action("tap", {"i": 1}, {"app": {}, "hash": "h"})
    env = mcp_server.call_tool("phone_audit_query", {"limit": 1})
    assert env["ok"] is True and env["data"]["entries"][0]["hash"] == "h"


def test_call_tool_unknown_name_errors():
    env = mcp_server.call_tool("phone_teleport", {})
    assert env["ok"] is False and env["error"]["code"] == "unknown_tool"


def test_register_registers_all_tools_on_fake_app(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    class FakeApp:
        def __init__(self):
            self.registered = []
            self.functions = {}

        def tool(self, name=None, description=None):
            self.registered.append(name)

            def deco(fn):
                self.functions[name] = fn
                return fn

            return deco

    app = FakeApp()
    build, _ = make_build()
    names = mcp_server._register(app, build=build)
    assert set(names) == set(mcp_server.TOOLS)
    assert "phone_tap" in app.registered
    assert all(
        not param.name.startswith("_")
        for fn in app.functions.values()
        for param in inspect.signature(fn).parameters.values()
    )
    assert list(inspect.signature(app.functions["phone_find"]).parameters) == ["selector"]
    assert list(inspect.signature(app.functions["phone_observe_ui"]).parameters) == ["tree", "relations", "screenshot"]
    env = app.functions["phone_observe_ui"]()
    assert env["ok"] is True and env["capability"] == "ui.observe"


def test_register_fastmcp_tools_have_named_arguments_when_sdk_available():
    pytest.importorskip("mcp")
    from mcp.server.fastmcp import FastMCP

    app = FastMCP("phonectl-test")
    mcp_server._register(app)
    tool = app._tool_manager.get_tool("phone_observe_ui")
    assert tool is not None
    assert set(tool.parameters["properties"]) == {"tree", "relations", "screenshot"}
    assert "kwargs" not in tool.parameters["properties"]


def test_serve_raises_capability_unavailable_without_sdk(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("no mcp")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(errors.CapabilityUnavailableError):
        mcp_server.serve()


# Task 10: clipboard, intent, packages MCP tools

class FakePackageBackend(FakeBackend):
    def packages_list(self, include_system=False):
        return ["com.example.a", "com.example.b"]

    def capabilities(self):
        return caps.make(packages_list=True, packages_stop=True, packages_clear=True,
                         observe_ui_tree=True, act_tap=True, requires_adb=True,
                         act_type=True, act_key=True, launch_app=True)


def make_build_with_packages():
    from phonectl.session import Session
    backend = FakePackageBackend()

    def build(cfg):
        return backend, Session(), FakeConn()

    return build, backend


def test_phone_clipboard_read_returns_unavailable_without_termux(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    build, _ = make_build()
    env = mcp_server.call_tool("phone_clipboard_read", {}, build)
    assert env["ok"] is False
    assert env["error"]["code"] == "capability_unavailable"


def test_phone_packages_list_returns_list(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    build, _ = make_build_with_packages()
    env = mcp_server.call_tool("phone_packages_list", {}, build)
    assert env["ok"] is True
    assert isinstance(env["data"]["packages"], list)


def test_unknown_tool_still_returns_err(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    build, _ = make_build()
    env = mcp_server.call_tool("phone_clipboard_read_UNKNOWN", {}, build)
    assert env["ok"] is False


# --- Task 6: new gesture MCP tools ---

@pytest.fixture
def build(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b, _ = make_build()
    return b


def test_phone_long_press_returns_ok(build):
    env = mcp_server.call_tool("phone_long_press", {"x": 100, "y": 200}, build)
    assert env["ok"] is True


def test_phone_scroll_until_returns_ok(build):
    env = mcp_server.call_tool("phone_scroll_until",
                               {"direction": "down", "text": "x", "max_scrolls": 1},
                               build)
    assert env["ok"] is True
