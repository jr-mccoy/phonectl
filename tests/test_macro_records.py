from phonectl.macro import records


def test_macro_run_record_shape():
    state = {"run_id": "run_1", "steps_run": 2, "outcome": "ok", "started_at": 4.0, "cancelled": False}
    rec = records.macro_run_record(state, _M("reply"), trigger="manual", now=lambda: 5.0)
    assert rec["kind"] == "macro_run"
    assert rec["run_id"] == "run_1" and rec["macro_name"] == "reply"
    assert rec["steps_run"] == 2 and rec["outcome"] == "ok"
    assert rec["cancelled"] is False


def test_append_and_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    records.append({"kind": "macro_run", "run_id": "r1"})
    records.append({"kind": "action", "action_id": "a1"})
    assert [r["run_id"] for r in records.read(kind="macro_run")] == ["r1"]


class _M:
    def __init__(self, name):
        self.name = name
