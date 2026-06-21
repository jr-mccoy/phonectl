from phonectl import results, errors


def test_ok_minimal():
    assert results.ok() == {"ok": True}


def test_ok_with_capability_provider_and_extra():
    out = results.ok(capability="ui.set_text", provider="adb",
                     snapshot_after="snap_def")
    assert out["ok"] is True
    assert out["capability"] == "ui.set_text"
    assert out["provider"] == "adb"
    assert out["snapshot_after"] == "snap_def"


def test_err_from_phonectl_error_maps_code_and_flags():
    out = results.err(errors.DeviceLockedError("device is locked, unlock it"),
                      user_action="Unlock the phone manually.")
    assert out["ok"] is False
    assert out["error"]["code"] == "device_locked"
    assert out["error"]["message"] == "device is locked, unlock it"
    assert out["error"]["retryable"] is False
    assert out["error"]["requires_user"] is True
    assert out["error"]["user_action"] == "Unlock the phone manually."


def test_err_from_code_message_pair_defaults():
    out = results.err(("capability_unavailable", "notifications.reply not available"),
                      capability="notifications.reply",
                      user_action="Enable Notification Access in Android Settings.")
    assert out["ok"] is False
    assert out["error"]["code"] == "capability_unavailable"
    assert out["error"]["retryable"] is False
    assert out["capability"] == "notifications.reply"
