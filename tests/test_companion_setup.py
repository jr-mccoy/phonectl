from phonectl import companion_setup as cs

XML_OK = ('<?xml version="1.0"?><map>'
          '<string name="companion_token">abc123</string>'
          '<boolean name="cap_observe_ui_native" value="true"/></map>')

def test_parse_token_extracts_value():
    assert cs.parse_token(XML_OK) == "abc123"

def test_parse_token_missing_returns_none():
    assert cs.parse_token('<map><string name="stopped">x</string></map>') is None

def test_parse_token_blank_returns_none():
    assert cs.parse_token('<map><string name="companion_token"></string></map>') is None

def test_parse_token_garbage_returns_none():
    assert cs.parse_token("run-as: package not debuggable") is None

def test_step_shape():
    assert cs.step("verify", "done", "ok") == {
        "name": "verify", "ok": True, "status": "done", "message": "ok"}
