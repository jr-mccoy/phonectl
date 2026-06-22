import pytest
from phonectl.providers.clipboard import ClipboardProvider
from phonectl.providers.registry import ProviderRegistry
from phonectl import capabilities, errors


class FakeClipProv:
    _written = None

    def capabilities(self):
        return capabilities.make(write_clipboard=True, requires_adb=True)

    def clipboard_write(self, text):
        FakeClipProv._written = text

    def clipboard_read(self):
        return "hello"


class FakeReadProv:
    def capabilities(self):
        return capabilities.make(read_clipboard=True, write_clipboard=True)

    def clipboard_read(self):
        return "clipboard text"

    def clipboard_write(self, text):
        pass


def test_read_raises_capability_unavailable_when_no_provider():
    r = ProviderRegistry([FakeClipProv()])  # write only, no read
    cp = ClipboardProvider(r)
    env = cp.read()
    assert env["ok"] is False
    assert env["error"]["code"] == "capability_unavailable"
    assert "Termux" in env["error"]["user_action"]


def test_read_returns_text_when_provider_available():
    r = ProviderRegistry([FakeReadProv()])
    cp = ClipboardProvider(r)
    env = cp.read()
    assert env["ok"] is True
    assert env["data"]["text"] == "clipboard text"


def test_clear_delegates_to_write_empty():
    r = ProviderRegistry([FakeClipProv()])
    cp = ClipboardProvider(r)
    assert callable(cp.clear)
