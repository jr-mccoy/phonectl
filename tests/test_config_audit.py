import json
import pytest
from phonectl import config, audit


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    # no file: returns defaults only
    assert config.load().get("serial") is None
    config.save({"serial": "127.0.0.1:5555", "mode": "auto"})
    cfg = config.load()
    assert cfg["serial"] == "127.0.0.1:5555"
    assert config.get_mode(cfg) == "auto"


def test_default_mode_is_confirm(tmp_path, monkeypatch):
    # Finding 5: unconfirmed execution must be opt-in, not the default.
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    assert config.get_mode(config.load()) == "confirm"
    assert config.get_mode({}) == "confirm"


def test_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    assert audit.kill_switch_active() is False
    (tmp_path / "STOP").write_text("")
    assert audit.kill_switch_active() is True


def test_log_action_appends_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    audit.log_action("tap", {"i": 7}, {"app": {"package": "com.x"}, "hash": "abc"})
    lines = (tmp_path / "actions.jsonl").read_text().strip().splitlines()
    rec = json.loads(lines[0])
    assert rec["verb"] == "tap" and rec["target"] == {"i": 7}
    assert rec["app"] == "com.x" and rec["hash"] == "abc"
    assert "ts" in rec


def test_log_action_appends_multiple_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    audit.log_action("tap", {"i": 1}, {"app": {"package": "com.a"}, "hash": "h1"})
    audit.log_action("key", {"key": "back"}, {"app": {"package": "com.b"}, "hash": "h2"})
    lines = (tmp_path / "actions.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["app"] == "com.a"
    assert json.loads(lines[1])["app"] == "com.b"


def test_log_action_defensive_when_result_missing_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    audit.log_action("tap", {"i": 0}, {})
    rec = json.loads((tmp_path / "actions.jsonl").read_text().strip().splitlines()[0])
    assert rec["app"] == "" and rec["hash"] == ""


def test_log_action_default_redacted_scrubs_sensitive_target(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    audit.log_action(
        "type",
        {"text": "code 482913"},
        {"app": {"package": "com.x"}, "hash": "h"},
        request_id="r1",
    )
    rec = json.loads((tmp_path / "actions.jsonl").read_text().strip())
    assert rec["request_id"] == "r1"
    assert "482913" not in json.dumps(rec)
    assert "[REDACTED]" in rec["target"]["text"]


def test_log_action_redacted_is_noop_on_benign_target(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    audit.log_action(
        "tap",
        {"selector": {"text": "Wi-Fi"}},
        {"app": {"package": "com.x"}, "hash": "h"},
    )
    rec = json.loads((tmp_path / "actions.jsonl").read_text().strip())
    assert rec["target"] == {"selector": {"text": "Wi-Fi"}}


def test_log_action_metadata_level_drops_target(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"audit_level": "metadata"})
    audit.log_action(
        "tap",
        {"i": 7},
        {"app": {"package": "com.x"}, "hash": "h"},
        request_id="r2",
    )
    rec = json.loads((tmp_path / "actions.jsonl").read_text().strip())
    assert "target" not in rec and rec["verb"] == "tap" and rec["request_id"] == "r2"


def test_log_action_none_level_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"audit_level": "none"})
    audit.log_action("tap", {"i": 1}, {"app": {"package": "com.x"}, "hash": "h"})
    assert not (tmp_path / "actions.jsonl").exists()


def test_log_action_full_level_keeps_raw_target(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"audit_level": "full"})
    audit.log_action("type", {"text": "code 482913"}, {"app": {}, "hash": "h"})
    rec = json.loads((tmp_path / "actions.jsonl").read_text().strip())
    assert rec["target"]["text"] == "code 482913"


def test_read_entries_returns_last_n(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"audit_level": "metadata"})
    for n in range(5):
        audit.log_action(
            "tap", {"i": n}, {"app": {"package": "com.x"}, "hash": f"h{n}"}
        )
    last2 = audit.read_entries(limit=2)
    assert [e["hash"] for e in last2] == ["h3", "h4"]


def test_purge_removes_log_and_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    audit.log_action("tap", {"i": 0}, {"app": {}, "hash": "h"})
    assert audit.purge() == 1
    assert not (tmp_path / "actions.jsonl").exists()
    assert audit.purge() == 0


def test_export_redacts_targets(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    config.save({"audit_level": "full"})
    audit.log_action("type", {"text": "code 482913"}, {"app": {}, "hash": "h"})
    out = tmp_path / "bundle.json"
    audit.export(str(out), redacted=True)
    assert "482913" not in out.read_text()


def test_coerce_and_set_typed():
    cfg = {}
    config.coerce_and_set(cfg, "companion_port", "8765")
    assert cfg["companion_port"] == 8765 and isinstance(cfg["companion_port"], int)
    config.coerce_and_set(cfg, "companion_timeout", "1.5")
    assert cfg["companion_timeout"] == 1.5
    config.coerce_and_set(cfg, "companion_token", "abc")
    assert cfg["companion_token"] == "abc"


def test_coerce_and_set_rejects_unknown_key():
    with pytest.raises(KeyError):
        config.coerce_and_set({}, "not_a_real_key", "x")

