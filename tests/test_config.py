"""Config tests for companion defaults (Plan 4.3)."""
import pytest


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


# ── Crash-safe state files (audit D1) ──────────────────────────────────────

def test_load_survives_a_corrupt_config_file(tmp_path, monkeypatch):
    # config.load() runs on every command, so raising here took out even
    # `phonectl doctor` — the command whose job is diagnosing a broken install.
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import config
    (tmp_path / "config.json").write_text('{"mode": "auto"')   # truncated write
    cfg = config.load()
    assert cfg["companion_host"] == "127.0.0.1"       # defaults intact
    assert config.get_mode(cfg) == "confirm"          # falls back to the SAFE mode


def test_save_never_leaves_a_partial_config_behind(tmp_path, monkeypatch):
    # The atomic write means an interrupted save keeps the previous good config.
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    import json
    from phonectl import config, state
    config.save({"mode": "auto", "serial": "good"})

    def boom(*a, **k):
        raise OSError("no space left on device")

    real = state.write_json
    monkeypatch.setattr(state, "write_json",
                        lambda p, o, **kw: real(p, o, _replace=boom, **kw))
    with pytest.raises(OSError):
        config.save({"mode": "auto", "serial": "new"})
    assert json.loads((tmp_path / "config.json").read_text())["serial"] == "good"
    assert [f.name for f in tmp_path.iterdir()] == ["config.json"]
