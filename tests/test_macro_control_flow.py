from droidjig.macro import schema
from droidjig.macro.engine import Engine


def _eng(rec, run_action=None):
    def default_ra(verb, fn, target, **kw):
        rec.append((verb, target))
        return {"ok": True, "data": {"hash": "h"}}
    return Engine(build=lambda cfg: (None, None, None),
                  run_action=run_action or default_ra,
                  fn_for=lambda step, scopes: (lambda b, s: None),
                  sleep=lambda s: None)


def test_if_then_branch():
    rec = []
    m = schema.parse({"name": "m", "actions": [
        {"type": "if", "condition": {"type": "always"},
         "then": [{"type": "tap", "target": {"i": 1}}],
         "else": [{"type": "tap", "target": {"i": 2}}]}]})
    _eng(rec).run(m)
    assert rec == [("tap", {"i": 1})]


def test_for_each_iterates():
    rec = []
    m = schema.parse({"name": "m", "variables": {"rows": ["a", "b", "c"]}, "actions": [
        {"type": "for_each", "in": "${rows}", "as": "row",
         "do": [{"type": "type", "target": {"text": "${row}"}}]}]})
    _eng(rec).run(m)
    assert [t["text"] for _, t in rec] == ["a", "b", "c"]


def test_loop_bounded_by_max_iterations():
    rec = []
    m = schema.parse({"name": "m", "actions": [
        {"type": "loop", "while": {"type": "always"}, "max_iterations": 3,
         "do": [{"type": "tap", "target": {"i": 0}}]}]})
    _eng(rec).run(m)
    assert len(rec) == 3


def test_retry_succeeds_after_transient_busy():
    calls = {"n": 0}

    def flaky(verb, fn, target, **kw):
        calls["n"] += 1
        if calls["n"] < 2:
            return {"ok": False, "error": {"code": "busy", "retryable": True}}
        return {"ok": True, "data": {"hash": "h"}}

    rec = []
    m = schema.parse({"name": "m", "actions": [
        {"type": "retry", "max_attempts": 3, "backoff_seconds": 0,
         "do": [{"type": "tap", "target": {"i": 0}}]}]})
    out = _eng(rec, run_action=flaky).run(m)
    assert out["ok"] is True and calls["n"] == 2


def test_try_runs_finally_even_on_failure():
    rec = []

    def failing(verb, fn, target, **kw):
        rec.append((verb, target))
        if target.get("i") == 9:
            return {"ok": False, "error": {"code": "guarded_action"}}
        return {"ok": True, "data": {}}

    m = schema.parse({"name": "m", "actions": [
        {"type": "try", "do": [{"type": "tap", "target": {"i": 9}}],
         "finally": [{"type": "tap", "target": {"i": 0}}]}]})
    _eng(rec, run_action=failing).run(m)
    assert ("tap", {"i": 0}) in rec  # finally ran
