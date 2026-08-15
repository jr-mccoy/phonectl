from droidjig.macro import variables as V


def test_read_order_runtime_beats_macro():
    s = V.Scopes(runtime={"x": "r"}, macro={"x": "m", "y": "ym"})
    assert s.get("x") == "r"
    assert s.get("y") == "ym"
    assert s.get("missing", "d") == "d"


def test_set_targets_named_scope():
    s = V.Scopes()
    s.set("a", "1")
    s.set("b", "2", scope="macro")
    assert s.get("a") == "1"
    assert s.runtime == {"a": "1"} and s.macro == {"b": "2"}


def test_interpolate_substitutes():
    s = V.Scopes(runtime={"name": "Sam"})
    assert V.interpolate("Hi ${name}!", s) == "Hi Sam!"
    assert V.interpolate("none ${gone}", s) == "none "


def test_redacted_view_masks_secrets():
    s = V.Scopes(runtime={"x": "1"}, secret={"otp": "123456"})
    view = V.redacted_view(s)
    assert view["x"] == "1"
    assert view["otp"] != "123456"
    assert s.is_secret("otp") is True
