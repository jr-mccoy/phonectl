import pytest
from phonectl.providers.accessibility import AccessibilityProvider
from phonectl.providers.transport import LoopbackTransport
from phonectl import ui_parser


# --- Task 2: capabilities ---

def test_capabilities_all_relevant_true_when_available():
    p = AccessibilityProvider(LoopbackTransport({}))
    caps = p.capabilities()
    for key in ("observe_ui_native", "observe_ui_events", "act_set_text_native",
                "act_gesture_native", "act_semantic_action",
                "observe_ui_tree", "act_tap", "act_type", "act_key", "launch_app"):
        assert caps[key] is True


def test_capabilities_all_false_when_unavailable():
    p = AccessibilityProvider(LoopbackTransport({}, available=False))
    assert all(v is False for v in p.capabilities().values())


# --- Task 3: native tree + ui_dump ---

def _native_handler(_params):
    return {
        "windows": [{"id": 1, "type": "application", "package": "com.android.settings",
                     "nodes": [{"node_id": "n1", "text": "Network & internet",
                                "class": "android.widget.TextView", "content_desc": "",
                                "bounds": [0, 200, 1080, 320], "clickable": True,
                                "enabled": True}]}]
    }


def test_ui_dump_returns_parseable_compat_xml():
    p = AccessibilityProvider(LoopbackTransport({"observe_native": _native_handler}))
    elements = ui_parser.parse_elements(p.ui_dump())
    assert any(e.get("text") == "Network & internet" for e in elements)
