# phonectl — idempotency-cache TTL eviction

**Date:** 2026-06-23
**Status:** Design spec (follow-up to the daemon async-job model). Required before its implementation plan.
**Author:** Jeremy McCoy (with Claude)

**Reads with:**

- `docs/design/2026-06-22-phonectl-daemon-async-jobs-design.md` — the async-job model that
  introduced `JobRegistry` and relies on `runtime.run_action`'s idempotency replay. This spec closes the
  unbounded-growth follow-up that the async-jobs whole-branch review flagged.

This is a **design document** — goals, the problem, locked decisions, schemas, testing. No TDD tasks;
those live in the plan.

---

## 1. Problem

The long-lived daemon process holds two idempotency-related caches that **never evict**, so memory grows
without bound for the daemon's lifetime — counter to the autonomy north-star (a daemon meant to run for
days/weeks).

1. **`JobRegistry._jobs` + `_by_key`** (`src/phonectl/daemon/jobs.py`). Every `Job` is retained forever.
   `_dedupe_locked` checks `idempotency_ttl` for *dedupe decisions* but never deletes; one `Job` (plus a
   `_by_key` entry when keyed) accumulates per logical action.

2. **`runtime._idempotency_cache`** (`src/phonectl/runtime.py`). A module-global `dict[key -> env]` with
   **no timestamp and no TTL at all**. Besides unbounded growth, it replays a cached result for a key
   *forever*: a key reused long after its original action would silently replay a stale envelope and never
   re-execute. This is the worse of the two — a latent correctness hole, not just memory.

## 2. Goals & non-goals

**Goals:**
- Memory stays flat under sustained activity (bounded by entries created within the TTL window plus
  in-flight jobs).
- The runtime cache stops replaying stale results past the TTL — an expired key re-executes.
- One shared TTL, no new config knob, deterministic and unit-testable (injected clock, no timers).

**Non-goals:**
- Unifying the two caches into one. They serve different layers (`JobRegistry` is daemon-only; the
  runtime cache also covers the in-process CLI and MCP paths) and unifying is a larger refactor.
- A hard max-count / LRU cap. TTL-based eviction is sufficient for the activity profile; a count cap would
  change dedupe semantics under load.
- A background sweeper thread. Eviction is opportunistic (on submit/insert), so no new thread to manage.

## 3. Locked decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Both caches** get TTL eviction. | The runtime cache is the worse leak (no TTL at all); fixing only `JobRegistry` leaves it. |
| 2 | **Opportunistic sweep** — evict on `submit()` (jobs) / on store (runtime), under the existing lock. | No background thread; deterministic; a quiescent daemon never accumulates because growth only happens on activity. |
| 3 | **Reuse `idempotency_ttl`** (config default 300.0s) as the single retention TTL for both caches. | No new knob; both layers expire in lockstep so the daemon's two dedupe layers never disagree. |
| 4 | **Only terminal jobs are evicted** (status `done`/`error`); queued/running are always kept. | Never drop an in-flight or pending job. |
| 5 | Eviction of a finished job makes `job_poll`/`phonectl job <id>` return `unknown_job` after the TTL. | Acceptable: a finished job stays reattachable for `idempotency_ttl` (default 5 min); that is the same window dedupe already uses. |

## 4. Design

### 4.1 `JobRegistry` (`src/phonectl/daemon/jobs.py`)

Add a private sweep run under `self._cv`:

```
def _sweep_locked(self):
    cutoff = self._now() - self._ttl
    expired = [jid for jid, job in self._jobs.items()
               if job.status in _TERMINAL
               and job.ts_finished is not None
               and job.ts_finished <= cutoff]
    for jid in expired:
        job = self._jobs.pop(jid, None)
        if job is not None and job.idempotency_key is not None \
                and self._by_key.get(job.idempotency_key) == jid:
            del self._by_key[job.idempotency_key]
```

Call `self._sweep_locked()` at the top of `submit()` (already holding `self._cv`), before dedupe and
enqueue. `_dedupe_locked` already returns `None` when `_jobs.get(jid)` is missing, so a swept key degrades
to "no dedupe → new job," which is correct. Removing the matching `_by_key` entry keeps the maps
consistent (no dangling key → evicted-id).

Boundary: a job exactly at the cutoff (`ts_finished == now - ttl`) is evicted (`<= cutoff`); this matches
"retained for *less than* ttl" the dedupe path uses (`now - ts_finished < ttl`). The two are consistent:
within `[0, ttl)` the job is both dedupe-eligible and retained; at/after `ttl` it is neither.

### 4.2 `runtime._idempotency_cache` (`src/phonectl/runtime.py`)

Change storage to `{key: (ts, env)}`. In `run_action`:

- **Lookup** (replace the current hit check):
  ```
  ttl = cfg.get("idempotency_ttl", 300.0)   # cfg is resolved just below; move resolution above the check
  if idempotency_key is not None and idempotency_key in _idempotency_cache:
      ts, cached = _idempotency_cache[idempotency_key]
      if now() - ts < ttl:
          replay = dict(cached)
          replay["idempotent_replay"] = True
          return replay
      del _idempotency_cache[idempotency_key]   # expired -> fall through and re-execute
  ```
  Note: `cfg` is currently resolved *after* the cache check; move `cfg = config.load() if cfg is None
  else cfg` above the check so the TTL is available. `now` is already a `run_action` parameter
  (default `time.time`).

- **Store** (replace the current store):
  ```
  if idempotency_key is not None:
      _sweep_idempotency_cache(now(), ttl)
      _idempotency_cache[idempotency_key] = (now(), dict(env))
  ```
  with a module-level helper:
  ```
  def _sweep_idempotency_cache(now_ts, ttl):
      for k in [k for k, (ts, _) in _idempotency_cache.items() if now_ts - ts >= ttl]:
          del _idempotency_cache[k]
  ```

## 5. Testing (handoff to plan)

**`tests/test_daemon_jobs.py`:**
- A terminal job past TTL is evicted on the next `submit()` — `get(old_id)` returns `None` AND its
  `_by_key` entry is gone (a later submit with the same key creates a *new* id).
- A terminal job within TTL survives a `submit()`.
- A queued/running job is never evicted by the sweep, regardless of age.
- Existing dedupe tests (`within_ttl`, `expires_after_ttl`, queue-cap) stay green.

**`tests/test_runtime.py`:**
- Replay within TTL still works (existing `test_idempotency_key_replays_first_envelope` stays green).
- After TTL, the same key **re-executes** (no `idempotent_replay`) and re-stores a fresh entry.
- After inserting many keys with an advancing clock, the cache size stays bounded (expired entries are
  swept on store), not monotonically growing.

Full suite must stay green (was 464 passed, 1 skipped).

## 6. Risks

- **Clock source skew between layers.** Both use wall-clock and the same `idempotency_ttl`, so they expire
  together. Tests inject `now` to stay deterministic.
- **`run_action` cfg-resolution move.** Moving `cfg` resolution above the cache check is behavior-neutral
  except it now always resolves cfg before a replay; replay paths previously skipped `config.load()`. This
  is cheap and the replay still returns early — no policy/audit runs on replay.
- **`_idempotency_cache` is module-global.** Tests that call `run_action` across cases must continue to
  `.clear()` it in setup (the existing test already does); the tuple shape change is internal.
