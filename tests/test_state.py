"""Crash-safe JSON state files (audit D1).

Every phonectl state file lives on a phone, where `kill -9`, a dead battery, and a
full disk are routine. Writes must not be able to leave a half-written file behind,
and a corrupt file must degrade to "no state" rather than taking the tool down.
"""
import json
import os

import pytest

from phonectl import state


# ── read_json: never raises ────────────────────────────────────────────────

def test_read_json_returns_the_stored_object(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"a": 1}')
    assert state.read_json(p, {}) == {"a": 1}


def test_read_json_missing_file_returns_default(tmp_path):
    assert state.read_json(tmp_path / "nope.json", {"d": 1}) == {"d": 1}


def test_read_json_truncated_file_returns_default(tmp_path):
    # The exact shape a kill -9 mid-write leaves behind.
    p = tmp_path / "s.json"
    p.write_text('{"mode": "auto"')
    assert state.read_json(p, {"d": 1}) == {"d": 1}


def test_read_json_empty_file_returns_default(tmp_path):
    # A full disk can leave a zero-byte file: created, never written.
    p = tmp_path / "s.json"
    p.write_text("")
    assert state.read_json(p, {"d": 1}) == {"d": 1}


def test_read_json_binary_garbage_returns_default(tmp_path):
    p = tmp_path / "s.json"
    p.write_bytes(b"\x00\xff\xfe not json at all")
    assert state.read_json(p, []) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_read_json_unreadable_file_returns_default(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"a": 1}')
    p.chmod(0o000)
    try:
        assert state.read_json(p, {"d": 1}) == {"d": 1}
    finally:
        p.chmod(0o644)


def test_read_json_directory_in_place_of_file_returns_default(tmp_path):
    p = tmp_path / "s.json"
    p.mkdir()
    assert state.read_json(p, {"d": 1}) == {"d": 1}


def test_read_json_wrong_toplevel_type_returns_default(tmp_path):
    # Valid JSON, wrong shape: a caller expecting a dict must not get a list.
    p = tmp_path / "s.json"
    p.write_text("[1, 2, 3]")
    assert state.read_json(p, {"d": 1}) == {"d": 1}
    # ...and a caller expecting a list must not get a dict.
    p.write_text('{"a": 1}')
    assert state.read_json(p, []) == []


def test_read_json_default_is_not_shared_between_calls(tmp_path):
    # A returned default must not be the caller's object, or one caller's mutation
    # silently becomes every later caller's starting state.
    p = tmp_path / "nope.json"
    default = {"a": 1}
    got = state.read_json(p, default)
    got["b"] = 2
    assert state.read_json(p, default) == {"a": 1}


# ── write_json: atomic ─────────────────────────────────────────────────────

def test_write_json_round_trips(tmp_path):
    p = tmp_path / "s.json"
    state.write_json(p, {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1}


def test_write_json_replaces_existing_content(tmp_path):
    p = tmp_path / "s.json"
    state.write_json(p, {"a": 1})
    state.write_json(p, {"b": 2})
    assert json.loads(p.read_text()) == {"b": 2}


def test_write_json_leaves_no_temp_files_behind(tmp_path):
    state.write_json(tmp_path / "s.json", {"a": 1})
    assert [f.name for f in tmp_path.iterdir()] == ["s.json"]


def test_write_json_failure_leaves_the_old_file_intact(tmp_path):
    # The point of the atomic write: an interrupted write must not destroy the
    # previous good state. Simulate the crash between temp-write and replace.
    p = tmp_path / "s.json"
    state.write_json(p, {"good": True})

    def boom(*a, **k):
        raise OSError("no space left on device")

    with pytest.raises(OSError):
        state.write_json(p, {"bad": True}, _replace=boom)
    assert json.loads(p.read_text()) == {"good": True}
    assert [f.name for f in tmp_path.iterdir()] == ["s.json"]   # temp cleaned up


def test_write_json_is_atomic_via_os_replace(tmp_path, monkeypatch):
    # A reader must never observe a partially-written file, which means the
    # visible path is only ever swapped by an atomic rename.
    seen = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: seen.append((a, b)) or real(a, b))
    state.write_json(tmp_path / "s.json", {"a": 1})
    assert seen, "write_json must publish via os.replace"


def test_write_json_indent_is_supported(tmp_path):
    # config.json is human-editable and was written with indent=2.
    p = tmp_path / "s.json"
    state.write_json(p, {"a": 1}, indent=2)
    assert "\n" in p.read_text()


def test_write_json_creates_parent_directories(tmp_path):
    p = tmp_path / "nested" / "deep" / "s.json"
    state.write_json(p, {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1}


# ── read_jsonl: tolerate a torn trailing line ──────────────────────────────

def test_read_jsonl_reads_all_rows(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text('{"a": 1}\n{"a": 2}\n')
    assert state.read_jsonl(p) == [{"a": 1}, {"a": 2}]


def test_read_jsonl_skips_a_torn_trailing_line(tmp_path):
    # Appending is not atomic: a crash mid-append tears the last line. The
    # complete records before it must still be readable.
    p = tmp_path / "log.jsonl"
    p.write_text('{"a": 1}\n{"a": 2}\n{"a":')
    assert state.read_jsonl(p) == [{"a": 1}, {"a": 2}]


def test_read_jsonl_skips_blank_and_malformed_lines(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text('{"a": 1}\n\n   \nnot json\n{"a": 2}\n')
    assert state.read_jsonl(p) == [{"a": 1}, {"a": 2}]


def test_read_jsonl_missing_file_is_empty(tmp_path):
    assert state.read_jsonl(tmp_path / "nope.jsonl") == []


def test_read_jsonl_skips_non_object_rows(tmp_path):
    # Callers index these rows as dicts; a bare scalar would raise downstream.
    p = tmp_path / "log.jsonl"
    p.write_text('{"a": 1}\n"just a string"\n3\nnull\n{"a": 2}\n')
    assert state.read_jsonl(p) == [{"a": 1}, {"a": 2}]
