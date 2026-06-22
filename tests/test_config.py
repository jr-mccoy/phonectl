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


def test_async_job_defaults_present(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import config
    cfg = config.load()
    assert cfg["act_timeout"] == 60.0
    assert cfg["sync_timeout"] == 15.0
    assert cfg["poll_interval"] == 0.5
    assert cfg["job_queue_max"] == 8
    assert cfg["idempotency_ttl"] == 300.0
