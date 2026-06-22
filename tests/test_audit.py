"""Tests for audit.kill_switch_active extra_checks (Plan 4.3)."""
from phonectl import audit


def test_kill_switch_extra_check_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    assert audit.kill_switch_active() is False
    assert audit.kill_switch_active(extra_checks=[lambda: True]) is True


def test_kill_switch_extra_check_exception_is_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))

    def boom():
        raise RuntimeError("socket down")

    # file sentinel absent + flaky check -> not stopped (does not wedge)
    assert audit.kill_switch_active(extra_checks=[boom]) is False


def test_kill_switch_file_takes_precedence_over_extra(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    (tmp_path / "STOP").write_text("")
    # Even if extra check says False, file wins
    assert audit.kill_switch_active(extra_checks=[lambda: False]) is True
