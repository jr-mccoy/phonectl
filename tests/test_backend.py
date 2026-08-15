from droidjig import backend, capabilities
from droidjig.adb_backend import AdbBackend


def test_adb_backend_satisfies_protocol_runtime_checkable():
    b = AdbBackend(serial="d")
    assert isinstance(b, backend.Backend)


def test_adb_capabilities_shape_and_values():
    caps = AdbBackend(serial="d").capabilities()
    assert set(caps) == set(capabilities.CAPABILITY_KEYS)
    assert caps["observe_ui_tree"] is True
    assert caps["act_tap"] is True
    assert caps["send_intent"] is True
    assert caps["requires_adb"] is True
    assert caps["read_notifications"] is False
    assert caps["persistent_events"] is False
