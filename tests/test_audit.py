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


def test_read_entries_skips_a_torn_trailing_line(tmp_path, monkeypatch):
    # Appends are not atomic: a crash mid-append tears the last line. The complete
    # records before it must still be readable (audit D1).
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import audit
    (tmp_path / "actions.jsonl").write_text(
        '{"verb": "tap", "ts": 1}\n{"verb": "type", "ts": 2}\n{"verb":')
    entries = audit.read_entries()
    assert [e["verb"] for e in entries] == ["tap", "type"]
