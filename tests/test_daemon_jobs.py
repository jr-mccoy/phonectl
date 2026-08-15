from droidjig import errors, results
from droidjig.daemon.jobs import Job, JobRegistry


def _ok_runner(method, params):
    return results.ok(capability="test.run", data={"method": method, "p": params})


def _counting_runner():
    calls = {"n": 0}
    def run(method, params):
        calls["n"] += 1
        return results.ok(capability="test.run", data={"n": calls["n"]})
    return run, calls


def test_submit_returns_id_and_queues():
    reg = JobRegistry(_ok_runner)
    jid = reg.submit("observe", {})
    job = reg.get(jid)
    assert job is not None
    assert job.status == "queued"
    assert job.method == "observe"


def test_run_next_executes_and_stores_result():
    reg = JobRegistry(_ok_runner)
    jid = reg.submit("act", {"verb": "tap"})
    assert reg.run_next() is True
    job = reg.get(jid)
    assert job.status == "done"
    assert job.result_env["ok"] is True
    assert job.result_env["data"]["method"] == "act"


def test_run_next_returns_false_when_empty():
    reg = JobRegistry(_ok_runner)
    assert reg.run_next() is False


def test_failed_envelope_sets_error_status():
    def fail_runner(method, params):
        return results.err(("boom", "nope"))
    reg = JobRegistry(fail_runner)
    jid = reg.submit("act", {})
    reg.run_next()
    assert reg.get(jid).status == "error"


def test_runner_exception_becomes_internal_error():
    def raiser(method, params):
        raise RuntimeError("kaboom")
    reg = JobRegistry(raiser)
    jid = reg.submit("act", {})
    reg.run_next()
    job = reg.get(jid)
    assert job.status == "error"
    assert job.result_env["error"]["code"] == "internal_error"


def test_dedupe_returns_same_job_for_inflight_key():
    run, calls = _counting_runner()
    reg = JobRegistry(run)
    j1 = reg.submit("act", {"idempotency_key": "k1"})
    j2 = reg.submit("act", {"idempotency_key": "k1"})  # still queued -> same job
    assert j1 == j2
    reg.run_next()
    assert calls["n"] == 1  # ran once


def test_dedupe_returns_finished_job_within_ttl():
    run, calls = _counting_runner()
    t = {"now": 1000.0}
    reg = JobRegistry(run, idempotency_ttl=300.0, now=lambda: t["now"])
    j1 = reg.submit("act", {"idempotency_key": "k1"})
    reg.run_next()
    t["now"] = 1100.0  # 100s later, within ttl
    j2 = reg.submit("act", {"idempotency_key": "k1"})
    assert j1 == j2
    assert calls["n"] == 1  # not re-run


def test_dedupe_expires_after_ttl():
    run, calls = _counting_runner()
    t = {"now": 1000.0}
    reg = JobRegistry(run, idempotency_ttl=300.0, now=lambda: t["now"])
    reg.submit("act", {"idempotency_key": "k1"})
    reg.run_next()
    t["now"] = 1400.0  # 400s later, past ttl
    reg.submit("act", {"idempotency_key": "k1"})
    reg.run_next()
    assert calls["n"] == 2  # re-run after expiry


def test_queue_cap_raises_busy():
    reg = JobRegistry(_ok_runner, queue_max=2)
    reg.submit("act", {"idempotency_key": "a"})
    reg.submit("act", {"idempotency_key": "b"})
    try:
        reg.submit("act", {"idempotency_key": "c"})
        assert False, "expected BusyError"
    except errors.BusyError:
        pass


def test_worker_thread_drains_submitted_job():
    import time as _t
    reg = JobRegistry(_ok_runner)
    reg.start()
    try:
        jid = reg.submit("observe", {})
        deadline = _t.monotonic() + 2.0
        while _t.monotonic() < deadline and reg.get(jid).status != "done":
            _t.sleep(0.01)
        assert reg.get(jid).status == "done"
    finally:
        reg.stop()


def test_stop_is_idempotent_and_joins():
    reg = JobRegistry(_ok_runner)
    reg.start()
    reg.stop()
    reg.stop()  # must not raise


def test_submit_evicts_terminal_job_past_ttl():
    run, calls = _counting_runner()
    t = {"now": 1000.0}
    reg = JobRegistry(run, idempotency_ttl=300.0, now=lambda: t["now"])
    old = reg.submit("act", {"idempotency_key": "k1"})
    reg.run_next()                       # k1 job -> done at t=1000
    assert reg.get(old) is not None
    t["now"] = 1400.0                    # 400s later, past ttl
    new = reg.submit("act", {"idempotency_key": "k2"})  # triggers sweep
    assert reg.get(old) is None          # evicted from _jobs
    assert "k1" not in reg._by_key       # and from _by_key
    assert new != old


def test_submit_keeps_terminal_job_within_ttl():
    reg = JobRegistry(_ok_runner, idempotency_ttl=300.0, now=lambda: 1000.0)
    old = reg.submit("act", {"idempotency_key": "k1"})
    reg.run_next()
    reg.submit("act", {"idempotency_key": "k2"})   # sweep runs, same instant
    assert reg.get(old) is not None      # within ttl -> survives


def test_sweep_never_evicts_queued_or_running_job():
    # A queued job has ts_finished=None and must never be swept, however old the clock.
    t = {"now": 1000.0}
    reg = JobRegistry(_ok_runner, idempotency_ttl=300.0, now=lambda: t["now"])
    queued = reg.submit("act", {"idempotency_key": "k1"})   # queued, not run
    t["now"] = 999999.0
    reg.submit("act", {"idempotency_key": "k2"})            # triggers sweep
    assert reg.get(queued) is not None   # queued job survives regardless of age
