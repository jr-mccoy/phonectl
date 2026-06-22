from phonectl.daemon import records


def test_build_record_from_ok_envelope():
    env = {"ok": True, "data": {"hash": "abc"}, "provider": "AdbBackend",
           "risk_level": "low", "request_id": "r9"}
    rec = records.build_record(
        env, {"verb": "tap", "target": {"i": 3}, "yes": True, "parent_task_id": "t1"},
        action_id="a1", now=lambda: 123.0)
    assert rec["action_id"] == "a1"
    assert rec["parent_task_id"] == "t1"
    assert rec["request_id"] == "r9"
    assert rec["verb"] == "tap" and rec["target"] == {"i": 3}
    assert rec["provider"] == "AdbBackend"
    assert rec["snapshot_before"] is None
    assert rec["snapshot_after"] == {"hash": "abc"}
    assert rec["outcome"] == "ok"
    assert rec["user_approved"] is True
    assert rec["retries"] == 0


def test_build_record_from_error_envelope():
    env = {"ok": False, "error": {"code": "guarded_action"}, "request_id": "r1"}
    rec = records.build_record(env, {"verb": "type", "target": {"text": "x"}},
                               action_id="a2", now=lambda: 1.0)
    assert rec["outcome"] == "guarded_action"
    assert rec["snapshot_after"] is None
    assert rec["user_approved"] is False


def test_append_and_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    records.append({"action_id": "a1", "verb": "tap"})
    records.append({"action_id": "a2", "verb": "type"})
    rows = records.read()
    assert [r["action_id"] for r in rows] == ["a1", "a2"]
