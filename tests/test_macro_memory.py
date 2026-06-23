# tests/test_macro_memory.py
import pytest

from phonectl.macro import memory


def test_write_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    memory.write("device", {"android": "14", "oem": "samsung"})
    assert memory.read("device")["oem"] == "samsung"


def test_unknown_store_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    with pytest.raises(ValueError):
        memory.read("contacts")


def test_values_are_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    memory.update("failures", "note", "OTP is 123456 call 555-123-4567")
    stored = memory.read("failures")["note"]
    assert "123456" not in stored and "555-123-4567" not in stored  # both redacted (D12)


def test_export_and_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    memory.write("prefs", {"quiet_hours": "22:00-08:00"})
    assert "prefs" in memory.export()
    memory.delete("prefs")
    assert memory.read("prefs") == {}
