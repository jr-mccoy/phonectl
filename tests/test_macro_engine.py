from droidjig.macro import schema
from droidjig.macro.engine import Engine, CancellationToken


def _engine(recorder, **kw):
    def fake_run_action(verb, fn, target, **kwargs):
        recorder.append((verb, target))
        return {"ok": True, "data": {"hash": "h", "verb": verb}, "request_id": kwargs.get("request_id")}
    return Engine(build=lambda cfg: ("REG", "SESS", None),
                  run_action=fake_run_action,
                  fn_for=lambda step, scopes: (lambda b, s: None),
                  **kw)


def test_runs_sequential_phone_verbs():
    rec = []
    m = schema.parse({"name": "m", "actions": [
        {"type": "tap", "target": {"i": 0}},
        {"type": "launch", "package": "com.example"}]})
    out = _engine(rec).run(m)
    assert out["ok"] is True
    assert [v for v, _ in rec] == ["tap", "launch"]
    assert out["data"]["steps_run"] == 2
    assert out["data"]["run_id"].startswith("run_")


def test_set_and_interpolation_into_target():
    rec = []
    m = schema.parse({"name": "m", "actions": [
        {"type": "set", "var": "msg", "value": "hello"},
        {"type": "type", "target": {"text": "${msg}"}}]})
    _engine(rec).run(m)
    assert rec[-1][1]["text"] == "hello"


def test_stop_ends_run_early():
    rec = []
    m = schema.parse({"name": "m", "actions": [
        {"type": "tap", "target": {"i": 0}}, {"type": "stop"},
        {"type": "tap", "target": {"i": 1}}]})
    out = _engine(rec).run(m)
    assert out["data"]["steps_run"] == 1 and out["data"]["outcome"] == "ok"


def test_failed_action_ends_run_with_error_code():
    def failing(verb, fn, target, **kw):
        return {"ok": False, "error": {"code": "guarded_action"}}
    eng = Engine(build=lambda cfg: (None, None, None), run_action=failing,
                 fn_for=lambda step, scopes: (lambda b, s: None))
    m = schema.parse({"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]})
    out = eng.run(m)
    assert out["ok"] is False and out["data"]["outcome"] == "guarded_action"


def test_cancellation_before_step():
    rec = []
    tok = CancellationToken()
    tok.cancel()
    m = schema.parse({"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]})
    out = _engine(rec).run(m, token=tok)
    assert out["ok"] is False and out["error"]["code"] == "macro_cancelled"
    assert rec == []


def test_run_sets_parent_task_id_on_actions():
    seen = {}

    def ra(verb, fn, target, **kw):
        seen["parent"] = kw.get("parent_task_id")
        return {"ok": True, "data": {}}

    eng = Engine(build=lambda cfg: (None, None, None), run_action=ra,
                 fn_for=lambda step, scopes: (lambda b, s: None))
    out = eng.run(schema.parse({"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]}))
    assert seen["parent"] == out["data"]["run_id"]
