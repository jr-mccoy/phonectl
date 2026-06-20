import json
from phonectl import config, audit


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    assert config.load() == {}
    config.save({"serial": "127.0.0.1:5555", "mode": "confirm"})
    cfg = config.load()
    assert cfg["serial"] == "127.0.0.1:5555"
    assert config.get_mode(cfg) == "confirm"
    assert config.get_mode({}) == "auto"


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
