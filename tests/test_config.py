"""Config tests for companion defaults (Plan 4.3)."""


def test_companion_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import config
    cfg = config.load()
    assert cfg.get("companion_host", "127.0.0.1") == "127.0.0.1"
    assert cfg.get("companion_port") is None


def test_daemon_config_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import config
    cfg = config.load()
    assert cfg.get("daemon_host", "127.0.0.1") == "127.0.0.1"
    assert cfg.get("daemon_autostart", False) is False
