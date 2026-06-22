import pytest
from phonectl import capabilities


def test_schema_has_strategy_keys():
    for key in ("observe_ui_tree", "act_tap", "act_type", "send_intent",
                "read_notifications", "read_clipboard", "persistent_events",
                "requires_adb"):
        assert key in capabilities.CAPABILITY_KEYS


def test_make_fills_all_keys_default_false():
    caps = capabilities.make(observe_ui_tree=True, act_tap=True, requires_adb=True)
    assert set(caps) == set(capabilities.CAPABILITY_KEYS)
    assert caps["observe_ui_tree"] is True
    assert caps["read_notifications"] is False


def test_make_rejects_unknown_key():
    with pytest.raises(ValueError):
        capabilities.make(teleport=True)


def test_describe_mentions_available_and_unavailable():
    caps = capabilities.make(observe_ui_tree=True, read_notifications=False)
    text = capabilities.describe(caps)
    assert "observe_ui_tree" in text
    assert "read_notifications" in text


def test_new_capability_keys_exist():
    from phonectl import capabilities
    for key in ("packages_list", "packages_stop", "packages_clear",
                "intent_start", "intent_broadcast"):
        assert key in capabilities.CAPABILITY_KEYS
