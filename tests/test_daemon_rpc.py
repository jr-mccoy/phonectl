from droidjig.daemon import rpc


def test_register_and_dispatch_ok():
    reg = rpc.Registry()

    @reg.register("ping")
    def _ping(params, ctx):
        from droidjig import results
        return results.ok(capability="daemon.ping", data={"pong": True})

    out = reg.dispatch("ping", {}, ctx=None)
    assert out["ok"] is True and out["data"]["pong"] is True


def test_unknown_method_is_error_envelope():
    reg = rpc.Registry()
    out = reg.dispatch("nope", {}, ctx=None)
    assert out["ok"] is False
    assert out["error"]["code"] == "unknown_method"


def test_handler_droidjig_error_becomes_envelope():
    reg = rpc.Registry()

    @reg.register("boom")
    def _boom(params, ctx):
        from droidjig import errors
        raise errors.CapabilityUnavailableError("nope")

    out = reg.dispatch("boom", {}, ctx=None)
    assert out["ok"] is False and out["error"]["code"] == "capability_unavailable"


def test_handler_unexpected_error_is_internal_error():
    reg = rpc.Registry()

    @reg.register("kaboom")
    def _kaboom(params, ctx):
        raise RuntimeError("unexpected")

    out = reg.dispatch("kaboom", {}, ctx=None)
    assert out["ok"] is False and out["error"]["code"] == "internal_error"


def test_duplicate_registration_raises():
    reg = rpc.Registry()
    reg.register("dup")(lambda p, c: None)
    try:
        reg.register("dup")(lambda p, c: None)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_mutating_set_contains_stop_not_resume():
    # act is no longer in MUTATING: its submit is fast; the worker acquires the lock during execution
    # macro_run/cancel are also MUTATING (Phase 6.1)
    assert "stop" in rpc.MUTATING
    assert {"macro_run", "macro_cancel"} <= rpc.MUTATING
    # Finding 1: there is no resume RPC — resume is a human-only, out-of-band action.
    assert "resume" not in rpc.MUTATING
