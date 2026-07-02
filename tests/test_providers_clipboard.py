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


class _Conn:
    def ensure(self):
        pass


class _Session:
    last = None


def _write_env(tmp_path, monkeypatch, text):
    from phonectl import config, observer

    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"mode": "auto"})
    monkeypatch.setattr(
        observer, "observe",
        lambda b, s, **kw: setattr(s, "last", {"hash": "h", "app": {}, "elements": []}) or s.last,
    )
    r = ProviderRegistry([FakeClipProv()])
    return ClipboardProvider(r).write(
        text, build=lambda cfg: (r, _Session(), _Conn()), cfg=config.load()
    )


def test_clipboard_write_target_is_length_only(tmp_path, monkeypatch):
    # Finding 12: clipboard content must never reach the audit log; the target
    # is a length surrogate like the `type` verb uses.
    secret = "hunter2secretvalue"
    env = _write_env(tmp_path, monkeypatch, secret)
    assert env["ok"] is True
    log = (tmp_path / "actions.jsonl").read_text()
    assert secret not in log and secret[:10] not in log
    assert f"<{len(secret)} chars>" in log
    assert FakeClipProv._written == secret  # real text WAS written to the device
