# tests/test_macro_engine_gate.py
from phonectl.macro import schema
from phonectl.macro.engine import Engine


def _eng(decisions, recorder, unattended_confirm=False):
    def ra(verb, fn, target, **kw):
        recorder.append(verb)
        return {"ok": True, "data": {}}
    # gate returns scripted decisions per call
    seq = iter(decisions)
    return Engine(build=lambda cfg: (None, None, None), run_action=ra,
                  fn_for=lambda step, scopes: (lambda b, s: None),
                  gate=lambda step, scopes, unattended: next(seq),
                  confirm=lambda msg: unattended_confirm)


def test_allow_proceeds():
    rec = []
    m = schema.parse({"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]})
    out = _eng(["allow"], rec).run(m)
    assert out["ok"] is True and rec == ["tap"]


def test_deny_blocks_with_guarded_action():
    rec = []
    m = schema.parse({"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]})
    out = _eng(["deny"], rec).run(m)
    assert out["ok"] is False and out["data"]["outcome"] == "guarded_action" and rec == []


def test_confirm_in_unattended_blocks():
    rec = []
    m = schema.parse({"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]})
    out = _eng(["confirm"], rec).run(m, unattended=True)
    assert out["ok"] is False and out["data"]["outcome"] == "confirmation_required" and rec == []


def test_confirm_interactive_proceeds_on_yes():
    rec = []
    m = schema.parse({"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]})
    out = _eng(["confirm"], rec, unattended_confirm=True).run(m, unattended=False)
    assert out["ok"] is True and rec == ["tap"]


def test_unattended_default_gate_blocks_without_grant(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))   # empty ledger -> no grants
    rec = []
    def ra(verb, fn, target, **kw):
        rec.append(verb); return {"ok": True, "data": {}}
    from phonectl.macro.engine import Engine
    from phonectl.macro import schema as _schema
    eng = Engine(build=lambda cfg: (None, None, None), run_action=ra,
                 fn_for=lambda step, scopes: (lambda b, s: None))   # NOTE: no gate= -> default gate
    m = _schema.parse({"name": "m", "actions": [{"type": "tap", "target": {"i": 0}}]})
    out = eng.run(m, unattended=True)
    assert out["ok"] is False and out["data"]["outcome"] == "confirmation_required" and rec == []
