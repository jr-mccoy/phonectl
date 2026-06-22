import pytest
from phonectl.providers.registry import ProviderRegistry


class FakeProv:
    def __init__(self, name, caps):
        self._name = name
        self._caps = caps

    def capabilities(self):
        return self._caps


def _caps(**kw):
    from phonectl import capabilities
    return capabilities.make(**kw)


def test_capabilities_merged():
    a = FakeProv("A", _caps(act_tap=True, requires_adb=True))
    b = FakeProv("B", _caps(read_clipboard=True))
    r = ProviderRegistry([a, b])
    merged = r.capabilities()
    assert merged["act_tap"] is True
    assert merged["read_clipboard"] is True
    assert merged["observe_ui_tree"] is False


def test_for_capability_returns_first_matching():
    a = FakeProv("A", _caps(act_tap=True))
    b = FakeProv("B", _caps(act_tap=True))
    r = ProviderRegistry([a, b])
    assert r.for_capability("act_tap") is a


def test_for_capability_returns_none_when_no_match():
    a = FakeProv("A", _caps(act_tap=True))
    r = ProviderRegistry([a])
    assert r.for_capability("read_clipboard") is None


def test_capabilities_by_provider_shape():
    a = FakeProv("A", _caps(act_tap=True))
    r = ProviderRegistry([a])
    items = r.capabilities_by_provider()
    assert len(items) == 1
    assert items[0]["provider"] == "FakeProv"
    assert items[0]["caps"]["act_tap"] is True


def test_empty_registry_has_all_false_capabilities():
    r = ProviderRegistry([])
    assert all(v is False for v in r.capabilities().values())


# Task 2 tests — Backend Protocol delegation

from phonectl import errors


class FakeAdbProv:
    serial = "fake:5555"
    _tapped = None

    def capabilities(self):
        return _caps(act_tap=True, observe_ui_tree=True, requires_adb=True,
                     launch_app=True, act_type=True, act_key=True,
                     observe_screenshot=True)

    def ui_dump(self):
        return "<hierarchy></hierarchy>"

    def window_dump(self):
        return ""

    def wm_size(self):
        return (1080, 2400)

    def screencap(self, path):
        return path

    def input_tap(self, x, y):
        FakeAdbProv._tapped = (x, y)

    def input_text(self, text):
        pass

    def input_swipe(self, x1, y1, x2, y2, ms=200):
        pass

    def input_key(self, keycode):
        pass

    def launch(self, package):
        pass

    def get_state(self):
        return "device"

    def wake(self):
        pass


def test_delegation_tap_sets_last_used():
    prov = FakeAdbProv()
    r = ProviderRegistry([prov])
    r.input_tap(100, 200)
    assert r.last_used == "FakeAdbProv"
    assert FakeAdbProv._tapped == (100, 200)


def test_delegation_serial_property():
    prov = FakeAdbProv()
    r = ProviderRegistry([prov])
    assert r.serial == "fake:5555"


def test_require_raises_capability_unavailable_when_no_provider():
    r = ProviderRegistry([])
    with pytest.raises(errors.CapabilityUnavailableError) as exc:
        r.input_tap(0, 0)
    assert "act_tap" in str(exc.value)


def test_getattr_delegates_adb_specific_helpers():
    prov = FakeAdbProv()
    r = ProviderRegistry([prov])
    r.wake()   # delegates via __getattr__ to FakeAdbProv.wake


def test_getattr_raises_when_no_adb_provider():
    r = ProviderRegistry([])
    with pytest.raises(AttributeError):
        r.wake()
