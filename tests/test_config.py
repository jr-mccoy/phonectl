"""Config tests for companion defaults (Plan 4.3)."""
import pytest


def test_companion_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    from droidjig import config
    cfg = config.load()
    assert cfg.get("companion_host", "127.0.0.1") == "127.0.0.1"
    assert cfg.get("companion_port") is None


def test_daemon_config_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    from droidjig import config
    cfg = config.load()
    assert cfg.get("daemon_host", "127.0.0.1") == "127.0.0.1"
    assert cfg.get("daemon_autostart", False) is False


def test_async_job_defaults_present(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    from droidjig import config
    cfg = config.load()
    assert cfg["act_timeout"] == 60.0
    assert cfg["sync_timeout"] == 15.0
    assert cfg["poll_interval"] == 0.5
    assert cfg["job_queue_max"] == 8
    assert cfg["idempotency_ttl"] == 300.0


# ── Crash-safe state files (audit D1) ──────────────────────────────────────

def test_load_survives_a_corrupt_config_file(tmp_path, monkeypatch):
    # config.load() runs on every command, so raising here took out even
    # `droidjig doctor` — the command whose job is diagnosing a broken install.
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    from droidjig import config
    (tmp_path / "config.json").write_text('{"mode": "auto"')   # truncated write
    cfg = config.load()
    assert cfg["companion_host"] == "127.0.0.1"       # defaults intact
    assert config.get_mode(cfg) == "confirm"          # falls back to the SAFE mode


def test_save_never_leaves_a_partial_config_behind(tmp_path, monkeypatch):
    # The atomic write means an interrupted save keeps the previous good config.
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    import json
    from droidjig import config, state
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


# ── Rename compatibility: phonectl -> droidjig ─────────────────────────────
# The project was renamed at 0.1.0. An existing install must keep working:
# its $PHONECTL_HOME and ~/.config/phonectl hold the paired companion token,
# the device serial and the audit log, and silently starting from an empty
# config would look like data loss.

def test_droidjig_home_is_preferred(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path / "new"))
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path / "old"))
    from droidjig import config
    assert config.config_dir() == tmp_path / "new"


def test_legacy_phonectl_home_is_honored(tmp_path, monkeypatch):
    monkeypatch.delenv("DROIDJIG_HOME", raising=False)
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path / "old"))
    from droidjig import config
    assert config.config_dir() == tmp_path / "old"


def test_legacy_config_dir_is_adopted_when_the_new_one_is_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("DROIDJIG_HOME", raising=False)
    monkeypatch.delenv("PHONECTL_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    legacy = tmp_path / ".config" / "phonectl"
    legacy.mkdir(parents=True)
    (legacy / "config.json").write_text('{"serial": "R5CT90ABCDE"}')
    from droidjig import config
    assert config.config_dir() == legacy
    assert config.load()["serial"] == "R5CT90ABCDE"   # the paired device survives


def test_new_config_dir_wins_once_it_exists(tmp_path, monkeypatch):
    monkeypatch.delenv("DROIDJIG_HOME", raising=False)
    monkeypatch.delenv("PHONECTL_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    (tmp_path / ".config" / "phonectl").mkdir(parents=True)
    (tmp_path / ".config" / "droidjig").mkdir(parents=True)
    from droidjig import config
    assert config.config_dir() == tmp_path / ".config" / "droidjig"


def test_fresh_install_uses_the_new_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("DROIDJIG_HOME", raising=False)
    monkeypatch.delenv("PHONECTL_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    from droidjig import config
    assert config.config_dir() == tmp_path / ".config" / "droidjig"
