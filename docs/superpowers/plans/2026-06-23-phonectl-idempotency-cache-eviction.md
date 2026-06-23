# Idempotency-Cache TTL Eviction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound both unbounded idempotency caches in the long-lived daemon (`JobRegistry._jobs`/`_by_key` and `runtime._idempotency_cache`) with an opportunistic TTL sweep, and stop the runtime cache from replaying stale results forever.

**Architecture:** Reuse the existing `idempotency_ttl` config (default 300s) as the single retention TTL. `JobRegistry` sweeps terminal jobs older than the TTL (from both maps) at the top of `submit()`, under its existing lock. `runtime._idempotency_cache` becomes `{key: (ts, env)}`, replays only within the TTL (else deletes and re-executes), and sweeps expired entries on store.

**Tech Stack:** Python ≥3.10, stdlib only, pytest.

## Global Constraints

- **Python ≥ 3.10; stdlib only** — no new dependencies.
- **One shared TTL:** reuse `idempotency_ttl` (config default 300.0s). No new config key.
- **Deterministic, no timers:** eviction is opportunistic (on submit/store), under existing locks; tests inject the clock.
- **Only terminal jobs (`done`/`error`) are evicted; queued/running are always kept.**
- **TTL boundary is consistent with dedupe:** retained/dedupe-eligible while `now - ts_finished < ttl`; evicted once `now - ts_finished >= ttl`.
- **Full suite was 464 passed, 1 skipped** — keep it green.

---

## File Structure

- **Modify** `src/phonectl/daemon/jobs.py` — add `_sweep_locked()`, call it at the top of `submit()`.
- **Modify** `src/phonectl/runtime.py` — `_idempotency_cache` becomes `{key: (ts, env)}`; TTL-gated replay + expiry-delete; `_sweep_idempotency_cache(now_ts, ttl)` helper; move `cfg` resolution above the cache check.
- **Modify** `tests/test_daemon_jobs.py` — sweep eviction tests.
- **Modify** `tests/test_runtime.py` — TTL expiry / re-execute / bounded-size tests.

---

## Task 1: JobRegistry TTL sweep

**Files:**
- Modify: `src/phonectl/daemon/jobs.py` (add `_sweep_locked`; call in `submit`)
- Test: `tests/test_daemon_jobs.py`

**Interfaces:**
- Consumes: existing `JobRegistry(run_fn, *, queue_max=8, idempotency_ttl=300.0, now=time.time, new_id=None)`, `Job`, `_TERMINAL = {"done","error"}`, `self._cv`, `self._jobs`, `self._by_key`, `self._now`, `self._ttl`.
- Produces: `JobRegistry._sweep_locked() -> None` (called under `self._cv`; evicts terminal jobs with `ts_finished <= now - ttl` from both `_jobs` and `_by_key`). `submit()` calls it before dedupe/enqueue.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_daemon_jobs.py` (reuses the existing `_ok_runner`, `_counting_runner` helpers):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_daemon_jobs.py -k "evicts or keeps_terminal or never_evicts" -v`
Expected: FAIL — `test_submit_evicts_terminal_job_past_ttl` fails (`reg.get(old)` is not None / `"k1" in reg._by_key`) because nothing sweeps yet. (The other two pass trivially today but lock in the contract.)

- [ ] **Step 3: Implement the sweep**

In `src/phonectl/daemon/jobs.py`, add the method (place it right after `_dedupe_locked`):

```python
    def _sweep_locked(self):
        cutoff = self._now() - self._ttl
        expired = [jid for jid, job in self._jobs.items()
                   if job.status in _TERMINAL
                   and job.ts_finished is not None
                   and job.ts_finished <= cutoff]
        for jid in expired:
            job = self._jobs.pop(jid, None)
            if (job is not None and job.idempotency_key is not None
                    and self._by_key.get(job.idempotency_key) == jid):
                del self._by_key[job.idempotency_key]
```

Call it at the top of `submit()`, inside the `with self._cv:` block, before the dedupe check:

```python
    def submit(self, method: str, params: dict) -> str:
        key = params.get("idempotency_key")
        with self._cv:
            self._sweep_locked()
            existing = self._dedupe_locked(key)
            ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_daemon_jobs.py -v`
Expected: PASS — the 3 new tests plus all existing job tests (dedupe within/after ttl, queue cap, worker) stay green.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/daemon/jobs.py tests/test_daemon_jobs.py
git commit -m "feat(daemon): JobRegistry evicts terminal jobs past idempotency_ttl"
```
Append the trailer:
`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## Task 2: runtime idempotency-cache TTL + expiry

**Files:**
- Modify: `src/phonectl/runtime.py` (cache tuple shape; TTL-gated replay; expiry delete; sweep-on-store; move cfg resolution above the check)
- Test: `tests/test_runtime.py`

**Interfaces:**
- Consumes: `runtime.run_action(verb, fn, target, *, build, yes=False, cfg=None, request_id=None, idempotency_key=None, gen_id=..., kill_switch=..., log=..., now=time.time, companion_transport=None)`; module global `_idempotency_cache`; `config.load`.
- Produces: `_idempotency_cache` keyed `{key: (ts, env)}`; `_sweep_idempotency_cache(now_ts, ttl) -> None`; replay only while `now() - ts < cfg["idempotency_ttl"]`, else delete + re-execute.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_runtime.py`, mirroring the existing `test_idempotency_key_replays_first_envelope` setup verbatim (it builds the runtime inline with the module's `FakeBackend`/`FakeSession`/`FakeConn` and monkeypatches `runtime.observer.observe`; `idempotency_ttl` already lives in `config.DEFAULTS` at 300.0, so do NOT pass `cfg` — rely on the default and inject `now` to control time). The env shape is `{"data": {"hash": ...}}` (run_action wraps the `fn` result), and `fn` returns a bare dict like `{"hash": ...}`.

```python
def test_idempotency_key_reexecutes_after_ttl(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    runtime._idempotency_cache.clear()
    backend = FakeBackend()
    sess = FakeSession()
    monkeypatch.setattr(runtime.observer, "observe",
                        lambda b, s, **kw: s.set_snapshot({"hash": "h"}))
    runs = []

    def fn(b, s):
        runs.append(1)
        return {"hash": f"after{len(runs)}"}

    build = lambda cfg: (backend, sess, FakeConn())
    clock = {"t": 1000.0}

    first = runtime.run_action("tap", fn, {"x": 1}, build=build,
                               idempotency_key="k1", gen_id=lambda: "r1",
                               now=lambda: clock["t"])
    assert runs == [1]
    assert "idempotent_replay" not in first

    clock["t"] = 1400.0            # 400s later, past the 300s default ttl
    second = runtime.run_action("tap", fn, {"x": 1}, build=build,
                                idempotency_key="k1", gen_id=lambda: "r2",
                                now=lambda: clock["t"])
    assert runs == [1, 1]                         # re-executed, not replayed
    assert "idempotent_replay" not in second


def test_idempotency_cache_sweeps_expired_on_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    runtime._idempotency_cache.clear()
    backend = FakeBackend()
    sess = FakeSession()
    monkeypatch.setattr(runtime.observer, "observe",
                        lambda b, s, **kw: s.set_snapshot({"hash": "h"}))

    def fn(b, s):
        return {"hash": "after"}

    build = lambda cfg: (backend, sess, FakeConn())
    clock = {"t": 0.0}

    # 5 distinct keys, each 1000s apart -> every prior entry is expired when the next stores
    for i in range(5):
        clock["t"] = i * 1000.0
        runtime.run_action("tap", fn, {"x": 1}, build=build,
                           idempotency_key=f"k{i}", gen_id=lambda: f"r{i}",
                           now=lambda: clock["t"])
    # store sweeps expired entries first, so only the most-recent key remains
    assert len(runtime._idempotency_cache) == 1
    assert "k4" in runtime._idempotency_cache
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_runtime.py -k "reexecutes_after_ttl or sweeps_expired" -v`
Expected: FAIL — `reexecutes_after_ttl` fails (`calls["n"] == 1`, replayed forever) because there is no TTL; `sweeps_expired` fails (`len == 5`) because nothing sweeps. They may also error on the cache being keyed to a bare env (tuple-unpacking) once Step 3 is partially applied — that's fine, run after full Step 3.

- [ ] **Step 3: Implement TTL + sweep**

In `src/phonectl/runtime.py`:

3a. Add a module-level sweep helper near `_idempotency_cache` (line ~12):

```python
def _sweep_idempotency_cache(now_ts, ttl):
    expired = [k for k, (ts, _env) in _idempotency_cache.items() if now_ts - ts >= ttl]
    for k in expired:
        del _idempotency_cache[k]
```

3b. In `run_action`, move the `cfg` resolution above the cache check and replace the hit check + store. Replace this current block:

```python
    if idempotency_key is not None and idempotency_key in _idempotency_cache:
        replay = dict(_idempotency_cache[idempotency_key])
        replay["idempotent_replay"] = True
        return replay

    cfg = config.load() if cfg is None else cfg
```

with:

```python
    cfg = config.load() if cfg is None else cfg
    ttl = cfg.get("idempotency_ttl", 300.0)
    if idempotency_key is not None and idempotency_key in _idempotency_cache:
        ts, cached = _idempotency_cache[idempotency_key]
        if now() - ts < ttl:
            replay = dict(cached)
            replay["idempotent_replay"] = True
            return replay
        del _idempotency_cache[idempotency_key]   # expired -> fall through and re-execute
```

3c. Replace the current store block:

```python
    if idempotency_key is not None:
        _idempotency_cache[idempotency_key] = dict(env)
    return env
```

with:

```python
    if idempotency_key is not None:
        _sweep_idempotency_cache(now(), ttl)
        _idempotency_cache[idempotency_key] = (now(), dict(env))
    return env
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_runtime.py -v`
Expected: PASS — the 2 new tests plus the existing `test_idempotency_key_replays_first_envelope` (replay within TTL, default clock → microseconds apart → within 300s).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/runtime.py tests/test_runtime.py
git commit -m "feat(runtime): TTL-gate + sweep _idempotency_cache (no stale replay, bounded growth)"
```
Append the trailer:
`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## Task 3: Full-suite regression

**Files:** none (verification only).

- [ ] **Step 1: Run the whole suite**

Run: `python -m pytest -q`
Expected: all pass (≥464 + 5 new, 1 skipped). The most likely straggler is a test that reads `runtime._idempotency_cache[key]` expecting a bare env rather than a `(ts, env)` tuple — only `test_idempotency_key_replays_first_envelope` touches the cache and it goes through `run_action` (not direct indexing), so it should be unaffected. Fix any straggler by going through `run_action` rather than indexing the cache directly.

- [ ] **Step 2: Confirm green, no commit needed if Step 1 made no changes.**

---

## Self-Review (completed by plan author)

- **Spec coverage:** §3 decision 1 (both caches) → Tasks 1 + 2; decision 2 (opportunistic sweep) → Task 1 `_sweep_locked` in submit, Task 2 `_sweep_idempotency_cache` on store; decision 3 (reuse idempotency_ttl) → both tasks read `idempotency_ttl`; decision 4 (only terminal evicted) → Task 1 `status in _TERMINAL` + `test_sweep_never_evicts_queued_or_running_job`; decision 5 (job_poll unknown after ttl) → covered by Task 1 eviction (get→None). §4.1/§4.2 code → Tasks 1/2 verbatim. §5 testing → per-task tests + Task 3. No gaps.
- **Placeholder scan:** All steps contain runnable code, with test scaffolding matched to the real `test_runtime.py` fixtures (`FakeBackend`/`FakeSession`/`FakeConn`, monkeypatched `observer.observe`). No TBD/placeholders.
- **Type consistency:** `_sweep_locked()` / `_sweep_idempotency_cache(now_ts, ttl)`; cache shape `{key: (ts, env)}` consistent across Task 2 lookup, store, and sweep; `idempotency_ttl` read identically (`cfg.get("idempotency_ttl", 300.0)` in runtime; constructor arg in JobRegistry). Consistent.
