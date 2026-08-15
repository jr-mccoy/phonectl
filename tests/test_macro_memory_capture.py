# tests/test_macro_memory_capture.py
from droidjig.macro import memory


def test_capture_selector_on_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    rec = {"kind": "action", "outcome": "ok", "verb": "tap",
           "target": {"selector": {"resource_id": "com.x:id/send"}, "matched_i": 14},
           "context": {"package": "com.x", "app_version": "2.1", "locale": "en"}}
    memory.capture_selector(rec)
    sels = memory.read("selectors")
    assert "com.x|2.1|en" in sels


def test_capture_selector_skips_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    rec = {"kind": "action", "outcome": "guarded_action",
           "target": {"selector": {"x": 1}}, "context": {"package": "com.x"}}
    memory.capture_selector(rec)
    assert memory.read("selectors") == {}


def test_capture_failure_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    rec = {"kind": "action", "outcome": "stale_snapshot", "verb": "tap"}
    memory.capture_failure(rec)
    memory.capture_failure(rec)
    assert memory.read("failures")["tap|stale_snapshot"] == 2
