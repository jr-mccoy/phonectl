# droidjig — Capability Sweep Findings (2026-07-04)

**Date:** 2026-07-04
**Scope:** Manual end-to-end capability sweep of the CLI against a live Samsung Galaxy S25
Ultra over Wireless Debugging — `observe`/`tap`/`type`/`swipe`/`key`/`launch`, `packages`,
`intent`, companion (`ocr`, `notifications`, `clipboard`, `tts`), `macro`/`job`/`daemon`,
`policy`/`autonomy`/`audit`/`memory`.
**Method:** Drove each subsystem through the real CLI against the real device/daemon (not
unit tests), cross-checked surprising responses against source, isolated root causes with
minimal repro. Most of the surface worked correctly end-to-end (index-based `tap`, `type`
sending raw keystrokes, `launch` auto-reobserving, the rate limiter genuinely blocking a
second high-risk verb, audit log redacting typed text to `<N chars>`, a hand-written 6-step
macro executing correctly). Two reproducible bugs surfaced; **both fixed 2026-07-04**
(see the Status line under each finding).

---

## Finding 1 — `autonomy grant --expires N` produces a dead-on-arrival grant

**Severity:** High · **Area:** CLI / Macro Autonomy Gate
**Status:** ✅ Fixed 2026-07-04 — the CLI now converts `--expires N` to `now + N` before
calling `grant()` / the `autonomy_grant` RPC (whose `expires_at` param was already
absolute-epoch semantics). Regression tests cover grant-visible-immediately,
lapse-after-duration, and absolute-timestamp-over-RPC.

**Problem.** `droidjig autonomy grant <macro> --max-risk <level> --expires N` treats `N` as
an absolute Unix epoch timestamp instead of "N seconds from now". `cli.py` passes
`args.expires` straight through as `expires_at`:

```
# src/droidjig/cli.py:1267
"expires_at": getattr(args, "expires", None)},
...
# src/droidjig/cli.py:1271
expires_at=getattr(args, "expires", None),
```

into `macro/autonomy.py:grant()`:

```
# src/droidjig/macro/autonomy.py:59-64
def grant(macro_name, *, max_risk, scope="all", expires_at=None, now, gen_id=None) -> dict:
    ...
    "granted_at": now, "expires_at": expires_at}
```

`autonomy list` (and presumably the unattended-mode gate) filters expired grants with:

```
# src/droidjig/macro/autonomy.py:29
if not (g.get("expires_at") is not None and g["expires_at"] <= now)]
```

Since `now` is a real epoch (~1.78e9) and any reasonable `--expires` value (e.g. `300`,
`3600`) is far smaller, the grant is `expires_at <= now` the instant it's created — it
disappears from `autonomy list` immediately.

**Repro:**
```
$ droidjig autonomy grant my-macro --max-risk medium --expires 300 --json
{"data": {"granted_at": 1783135956.36, "expires_at": 300.0, ...}}   # expires_at way in the past
$ droidjig autonomy list --json
{"data": {"grants": []}}   # gone immediately
```
Control (no `--expires`): grant appears in `list` normally and persists.

**Impact.** This is the exact mechanism the progressive-autonomy gate (Phase 6.3) exists
for. Any caller who sets a duration on a grant — the documented, presumably common usage —
gets a grant that is functionally a no-op. Unattended macro runs that check for a valid
grant will always fail to find one.

**Fix.** Convert `--expires` to `now + expires` before calling `grant()` (both the CLI
argument parsing/dispatch and wherever the daemon RPC handler does the equivalent). Add a
regression test asserting a grant created with `--expires` is still present in
`autonomy list` immediately afterward, and absent once real time (or an injected clock)
passes the duration.

---

## Finding 2 — `macro run` can report a client-side timeout after the run already succeeded

**Severity:** Medium · **Area:** Daemon RPC / Macro Engine
**Status:** ✅ Fixed 2026-07-04 — took option 2: `macro_run` is now an async daemon job
(`JobRegistry`, like `act`/`observe`), and the CLI drives it via `submit_and_wait` with
short poll RPCs (overall deadline config `macro_timeout`, default 600 s; on expiry the
envelope reports the job still running with the id to query). Also serializes macro steps
under the daemon's single-writer lock, which the old synchronous handler bypassed.

**Problem.** Running a multi-step macro (6 steps: `launch` + 5 `tap`s) via
`droidjig macro run <path> --yes --json` over the daemon returned:

```
{"error": {"code": "timeout", "message": "daemon call 'macro_run' timed out"}}
```

but `droidjig macro status --json` afterward showed the same run
(`macro_name` matched, `outcome: "ok"`, `steps_run: 6`) had completed successfully about 32
seconds after it started, and the on-device state (calculator result) matched what the
macro should have produced. The daemon RPC client's timeout for `macro_run` is shorter than
a realistic multi-step macro's wall-clock time (each step re-observes with settle/retry
delays that add up across steps).

**Impact.** A caller — human or an agent driving droidjig — that only reads the immediate
`macro run` response will conclude the macro failed or hung, when it actually completed
correctly. Anything scripted on top of `macro run` risks false-failure handling or
duplicate re-runs of an already-successful macro.

**Fix options (pick one):**
1. Raise the client-side RPC timeout specifically for `macro_run` (or make it scale with
   step count), or
2. Make `macro run` submit-and-poll internally the same way `--detach` + `job --wait`
   already does, so the CLI never blocks past a fixed RPC deadline.

In the meantime, callers should prefer `macro run --detach` and poll `job`/`macro status`
for any macro with more than a couple of steps.

---

## Non-bugs worth noting

- `ocr screen` failed consistently (`observe_failed — companion screen OCR failed or
  returned a stale response`) despite `companion status` reporting full pairing
  (`installed/accessibility/socket/token_paired` all `true`). Not root-caused here — worth a
  closer look if OCR is load-bearing for downstream work (e.g. Logos screen-text
  extraction).
- `clipboard read` and `tts speak` both require Termux:API (`droidjig setup termux-api`);
  ADB alone can't do either. `clipboard write` works fine over ADB.
- `droidjig observe` (and raw `uiautomator dump`) fails with "screen not idle" whenever the
  foreground app is a busy terminal (e.g. this same Termux session) — uiautomator's
  idle-state detector never settles against continuously-updating terminal output. This is
  expected given the shared-screen setup, not a droidjig defect; pressing `HOME` before
  observing resolves it.
