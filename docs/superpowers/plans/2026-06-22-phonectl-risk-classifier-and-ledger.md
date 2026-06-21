# phonectl Risk Classifier & Risk Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Implementation status:** ✅ COMPLETE. Landed across `162744d` → `69b57c8` (`risk.py`, `policy.py`, `ratelimit.py`, runtime policy/rate gates, blocked-action audit, `phonectl policy explain`, and docs). Key shipped files: `src/phonectl/risk.py`, `src/phonectl/policy.py`, `src/phonectl/ratelimit.py`, `src/phonectl/runtime.py`, `src/phonectl/audit.py`, `src/phonectl/cli.py`, with coverage in `tests/test_risk.py`, `tests/test_policy.py`, `tests/test_ratelimit.py`, `tests/test_runtime.py`, `tests/test_config_audit.py`, and `tests/test_cli.py`.

**Plan 2.2 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). Second plan of Phase
2. Depends on **Plan 1.1** for the `errors` hierarchy (`GuardedActionError`/`RateLimitError`) and the
`results` envelope, on **Plan 1.2** for the rich element metadata (`password`, `text`, `content_desc`,
`package`) the classifier reads, and on **Plan 2.1** for the `runtime.run_action` funnel it plugs into. It
**supersedes `2026-06-21-phonectl-safety-completeness.md`**, generalizing that plan's flat guarded-package
denylist + single rate-limit into a multi-signal **risk ledger** with a per-class policy and an `explain`
output an agent can read before acting (strategy §8, §24).

**Goal:** Replace "deny if package in a list, cap at N/min" with a **risk classifier** that scores each action
`low|medium|high|critical` from multiple signals (foreground package, screen-text keywords like
pay/send/transfer/install/factory-reset, password fields, OTP-like content, the verb itself), a **policy** map
that turns each level into `allow|confirm|deny`, a `policy.explain(...)` the agent can read to learn *why*,
and **bucketed sliding-window rate limits** (`tap`/`type`/`launch`/`high_risk`/`global`) plus a
repeated-screen-hash stop. All of it slots into the **Plan-2.1 `run_action`** choke-point so there is exactly
one place safety is enforced.

**Architecture:** Three new **pure** modules. `risk.py` classifies a `(snapshot, verb, target)` into
`{level, reasons}` with no I/O (it only reads the observed snapshot dict). `policy.py` maps a level → decision
via the configured `risk_policy` and composes `explain(snapshot, verb, target, cfg)` (classify + decide +
reasons + recommended action). `ratelimit.py` is a pure bucketed sliding-window over a passed-in history list
(injectable clock) plus the repeated-hash detector. `runtime.run_action` (Plan 2.1) grows a policy + rate
gate between its confirm check and the act region: a `deny` raises `errors.GuardedActionError`, a `confirm`
without `--yes` raises `errors.ConfirmationRequiredError` (Plan 2.1's code) carrying the risk reasons, and a
rate-limit breach raises `errors.RateLimitError` — all surfaced through the existing envelope. The rate-limit
**history** is persisted by `runtime` (small JSON file in `PHONECTL_HOME`); `ratelimit.py` stays pure.

**Tech Stack:** Python 3 (stdlib only at runtime: `re`, `json`, `time`); `pytest` for tests; `adb` remains
the only external runtime dependency.

## Global Constraints

- **stdlib-only at runtime** (Python ≥ 3.9; `pytest` dev-only). No new dep.
- **ONLY `adb_backend.py` may touch adb/subprocess.** `risk`/`policy`/`ratelimit` are pure; `runtime`
  composes them and does the (tiny) rate-history file I/O — never `adb`.
- **`ui_parser.py` stays pure** (untouched). The classifier consumes the *parsed* snapshot, not raw XML.
- **Element index / selector / `(x,y)`** targeting unchanged; the classifier is verb-agnostic.
- **Every actuator `act()` re-observes** — unchanged; the policy gate runs on the freshly-observed snapshot
  inside `run_action` before `fn` executes.
- **Modes + kill-switch + (now) risk policy gate every mutating action through `run_action`** — the single
  choke-point from Plan 2.1.
- **Every action is audited** — unchanged; a blocked action is recorded with its block reason (Task 6).
- **Structured-result invariant (Plan 1.1):** `deny`/`confirm`/`rate_limited` outcomes are distinguishable,
  actionable typed errors surfaced as `results.err(...)` with the risk `reasons` and `risk_level` attached.
- **Injectable seams** — `now` for `ratelimit`, `cfg` everywhere; tests isolate via
  `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **Risk levels are an ordered enum:** `low < medium < high < critical`. `risk.classify` returns the **max**
  severity among matched signals; `low` is the floor (every action has a level).
- **Stable signal reason strings** (appear in the envelope + `explain`, so do not rename): `guarded_package`,
  `password_field`, `payment_keyword`, `destructive_keyword`, `install_keyword`, `otp_like_content`,
  `high_risk_verb`. Each reason is `{ "signal": <key>, "detail": <human string> }`.
- **`risk.classify(snapshot, verb, target, *, guarded_packages=(), keywords=DEFAULT_KEYWORDS)
  -> {"level": str, "reasons": list[dict]}`** — pure; reads `snapshot["app"]["package"]` and
  `snapshot["elements"]` (text/content_desc/password).
- **`policy.decide(level, risk_policy) -> "allow"|"confirm"|"deny"`** and **`policy.explain(snapshot, verb,
  target, cfg) -> {"risk_level", "reasons", "decision", "recommended_action"}`** — pure; the agent-readable
  contract behind the `phone.policy.explain` MCP tool (Plan 2.3) and the `phonectl policy explain` verb.
- **`config.json` keys added:**
  - `risk_policy` — `{level: decision}`; default `{"low":"allow","medium":"allow","high":"confirm",
    "critical":"deny"}`.
  - `rate_limits` — `{bucket: per_minute}`; default `{"tap":120,"type":30,"swipe":120,"key":120,"launch":20,
    "high_risk":1,"global":180}`.
  - `guarded_packages` — list of package prefixes; now **one signal among many**, default `[]` (no behavior
    change for users who set it: a guarded package still scores `high`).
  No collision with Plan 2.1 (`audit_level`) or Plan 1.3 (`last_port`, `probe_ports`).
- **Rate-history file:** `runtime` persists recent allowed-action timestamps to
  `$PHONECTL_HOME/ratelimit.json` (`[{"bucket","ts"}]`), pruned to the window on each call. `ratelimit.py`
  itself is pure (operates on the in-memory list); `runtime` owns the read/write.

---

### Task 1: `risk.py` — pure multi-signal classifier

**Files:**
- Create: `src/phonectl/risk.py`
- Test: `tests/test_risk.py`

**Interfaces (PURE — no I/O):**
- `DEFAULT_KEYWORDS: dict[str, tuple[str, ...]]` — the tiered keyword table keyed by signal:
  `payment_keyword` (`pay`, `payment`, `purchase`, `transfer`, `checkout`, `card number`, `cvv`, `bank`) →
  contributes `critical`; `destructive_keyword` (`factory reset`, `erase all`, `wipe`, `delete account`,
  `uninstall`) → `critical`; `install_keyword` (`install`, `allow`, `grant`, `subscribe`, `send`) → `high`.
- `HIGH_RISK_VERBS: frozenset` — `{"launch"}` is *not* inherently high; the high-risk verb set is empty by
  default (verbs are low unless the screen says otherwise) — kept as a named seam for future verbs (e.g.
  `intent`/`uninstall` from Phase 3). Document it; default classification leans on screen signals.
- `classify(snapshot, verb, target, *, guarded_packages=(), keywords=DEFAULT_KEYWORDS) -> dict`:
  - `guarded_package` (→ `high`) when `snapshot["app"]["package"]` starts with any `guarded_packages` prefix.
  - `password_field` (→ `high`) when any element has `password` true.
  - keyword scan over each element's `text` + `content_desc` (lowercased): a hit on a `payment`/`destructive`
    keyword → `critical`; an `install` keyword → `high`.
  - `otp_like_content` (→ `medium`) when an element text matches `\b\d{4,8}\b` (a visible code).
  - floor `low`. Returns `{"level": <max>, "reasons": [{"signal","detail"}, ...]}` (reasons in detection
    order, deduped by signal).

- [x] **Step 1: Write the failing tests**

```python
# tests/test_risk.py
from phonectl import risk


def _snap(package="com.x", elements=None):
    return {"app": {"package": package}, "elements": elements or []}


def test_benign_screen_is_low():
    snap = _snap(elements=[{"text": "Wi-Fi", "content_desc": "", "password": False}])
    out = risk.classify(snap, "tap", {"i": 0})
    assert out["level"] == "low" and out["reasons"] == []


def test_guarded_package_is_high():
    snap = _snap(package="com.android.vending",
                 elements=[{"text": "Buy", "content_desc": "", "password": False}])
    out = risk.classify(snap, "tap", {"i": 0}, guarded_packages=["com.android.vending"])
    signals = {r["signal"] for r in out["reasons"]}
    assert "guarded_package" in signals
    assert out["level"] == "critical"          # 'Buy' also trips a payment keyword


def test_password_field_is_high():
    snap = _snap(elements=[{"text": "", "content_desc": "Password", "password": True}])
    out = risk.classify(snap, "type", {"text": "<x>"})
    assert out["level"] == "high"
    assert {r["signal"] for r in out["reasons"]} == {"password_field"}


def test_payment_keyword_is_critical():
    snap = _snap(elements=[{"text": "Confirm payment of $42", "content_desc": "", "password": False}])
    out = risk.classify(snap, "tap", {"i": 3})
    assert out["level"] == "critical"
    assert any(r["signal"] == "payment_keyword" for r in out["reasons"])


def test_destructive_keyword_is_critical():
    snap = _snap(elements=[{"text": "Factory reset", "content_desc": "", "password": False}])
    assert risk.classify(snap, "tap", {"i": 1})["level"] == "critical"


def test_otp_like_content_is_medium():
    snap = _snap(elements=[{"text": "Your code is 482913", "content_desc": "", "password": False}])
    out = risk.classify(snap, "tap", {"i": 0})
    assert out["level"] == "medium"
    assert any(r["signal"] == "otp_like_content" for r in out["reasons"])
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_risk.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.risk'`).

- [x] **Step 3: Write minimal implementation**

```python
# src/phonectl/risk.py
"""Pure multi-signal risk classifier (strategy §8.1, §24). Reads the observed
snapshot (package + element metadata) and the pending action, returns a level
plus explainable reasons. No I/O — the runtime feeds it a snapshot and the
configured guarded_packages."""
from __future__ import annotations

import re

_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_OTP_RE = re.compile(r"\b\d{4,8}\b")

DEFAULT_KEYWORDS = {
    "payment_keyword": ("pay", "payment", "purchase", "transfer", "checkout",
                        "card number", "cvv", "bank"),
    "destructive_keyword": ("factory reset", "erase all", "wipe", "delete account",
                            "uninstall"),
    "install_keyword": ("install", "allow", "grant", "subscribe", "send"),
}
_SIGNAL_LEVEL = {
    "payment_keyword": "critical",
    "destructive_keyword": "critical",
    "install_keyword": "high",
    "guarded_package": "high",
    "password_field": "high",
    "otp_like_content": "medium",
}
HIGH_RISK_VERBS = frozenset()   # named seam; Phase-3 verbs (intent/uninstall) join here


def _bump(level, candidate):
    return candidate if _ORDER[candidate] > _ORDER[level] else level


def classify(snapshot, verb, target, *, guarded_packages=(), keywords=DEFAULT_KEYWORDS) -> dict:
    level = "low"
    reasons: list[dict] = []
    seen = set()

    def add(signal, detail):
        nonlocal level
        if signal in seen:
            return
        seen.add(signal)
        reasons.append({"signal": signal, "detail": detail})
        level = _bump(level, _SIGNAL_LEVEL[signal])

    package = (snapshot.get("app", {}) or {}).get("package", "")
    if package and any(package.startswith(p) for p in guarded_packages):
        add("guarded_package", f"foreground package {package} is guarded")

    for el in snapshot.get("elements", []):
        if el.get("password"):
            add("password_field", "a password field is present on screen")
        blob = f"{el.get('text', '')} {el.get('content_desc', '')}".lower()
        for signal, words in keywords.items():
            if any(w in blob for w in words):
                add(signal, f"screen text matches {signal}")
        if _OTP_RE.search(el.get("text", "") or ""):
            add("otp_like_content", "screen shows an OTP-like code")

    if verb in HIGH_RISK_VERBS:
        add("high_risk_verb", f"{verb} is a high-risk verb")
    return {"level": level, "reasons": reasons}
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_risk.py -v`
Expected: PASS (6 tests).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/risk.py tests/test_risk.py
git commit -m "feat: pure multi-signal risk classifier (level + explainable reasons)"
```

---

### Task 2: `policy.py` — decision map + `explain`

**Files:**
- Create: `src/phonectl/policy.py`
- Test: `tests/test_policy.py`

**Interfaces (PURE):**
- `DEFAULT_POLICY = {"low":"allow","medium":"allow","high":"confirm","critical":"deny"}`.
- `decide(level, risk_policy=None) -> str` — looks up `level` in `risk_policy` (defaulting to
  `DEFAULT_POLICY`), returning `allow|confirm|deny`; unknown levels default to `confirm` (fail-safe).
- `explain(snapshot, verb, target, cfg) -> dict` — runs `risk.classify` with `cfg.get("guarded_packages",
  [])`, applies `decide` with `cfg.get("risk_policy")`, returns `{"risk_level", "reasons", "decision",
  "recommended_action"}` where `recommended_action` is a human string (`"allowed"`, `"re-run with --yes to
  confirm"`, or `"blocked by policy; override risk_policy to permit"`).

- [x] **Step 1: Write the failing tests**

```python
# tests/test_policy.py
from phonectl import policy


def _snap(package="com.x", elements=None):
    return {"app": {"package": package}, "elements": elements or []}


def test_decide_uses_default_policy():
    assert policy.decide("low") == "allow"
    assert policy.decide("high") == "confirm"
    assert policy.decide("critical") == "deny"


def test_decide_respects_override():
    assert policy.decide("high", {"high": "deny"}) == "deny"


def test_decide_unknown_level_is_confirm():
    assert policy.decide("weird") == "confirm"


def test_explain_low_screen_allows():
    out = policy.explain(_snap(elements=[{"text": "Wi-Fi"}]), "tap", {"i": 0}, {})
    assert out["risk_level"] == "low" and out["decision"] == "allow"


def test_explain_payment_denies_with_reasons():
    snap = _snap(elements=[{"text": "Confirm payment"}])
    out = policy.explain(snap, "tap", {"i": 0}, {})
    assert out["risk_level"] == "critical" and out["decision"] == "deny"
    assert any(r["signal"] == "payment_keyword" for r in out["reasons"])
    assert "blocked" in out["recommended_action"]


def test_explain_honors_config_guarded_and_policy():
    snap = _snap(package="com.bank.app", elements=[{"text": "Home"}])
    cfg = {"guarded_packages": ["com.bank"], "risk_policy": {"high": "deny"}}
    out = policy.explain(snap, "tap", {"i": 0}, cfg)
    assert out["decision"] == "deny"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_policy.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.policy'`).

- [x] **Step 3: Write minimal implementation**

```python
# src/phonectl/policy.py
"""Pure risk policy: level -> allow|confirm|deny, plus an agent-readable explain
(strategy §24). The agent reads explain() BEFORE acting so a blocked action is
understood rather than blindly retried."""
from __future__ import annotations

from phonectl import risk

DEFAULT_POLICY = {"low": "allow", "medium": "allow", "high": "confirm", "critical": "deny"}


def decide(level, risk_policy=None) -> str:
    policy = {**DEFAULT_POLICY, **(risk_policy or {})}
    return policy.get(level, "confirm")


def explain(snapshot, verb, target, cfg) -> dict:
    classified = risk.classify(snapshot, verb, target,
                               guarded_packages=cfg.get("guarded_packages", []))
    decision = decide(classified["level"], cfg.get("risk_policy"))
    recommended = {
        "allow": "allowed",
        "confirm": "re-run with --yes to confirm",
        "deny": "blocked by policy; override risk_policy to permit",
    }[decision]
    return {
        "risk_level": classified["level"],
        "reasons": classified["reasons"],
        "decision": decision,
        "recommended_action": recommended,
    }
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_policy.py -v`
Expected: PASS (6 tests).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/policy.py tests/test_policy.py
git commit -m "feat: pure risk policy decision map + agent-readable explain()"
```

---

### Task 3: `ratelimit.py` — pure bucketed sliding-window + repeated-hash stop

**Files:**
- Create: `src/phonectl/ratelimit.py`
- Test: `tests/test_ratelimit.py`

**Interfaces (PURE — operate on a passed-in history list; injectable clock):**
- `buckets_for(verb, level) -> list[str]` — the buckets an action counts against: always `["global", verb]`,
  plus `"high_risk"` when `level in ("high","critical")`.
- `prune(history, now, window=60.0) -> list[dict]` — drop records older than `now - window`.
- `check(history, verb, level, limits, now, window=60.0) -> tuple[bool, str|None]` — pure: for each bucket in
  `buckets_for`, count matching records within the window; return `(False, bucket)` for the first bucket at or
  over its limit, else `(True, None)`. Buckets with no configured limit are unbounded.
- `repeated_hash(history_hashes, threshold=3) -> bool` — `True` when the last `threshold` recorded screen
  hashes are identical (the "stop after repeated unchanged screen hashes" guard, strategy §8.2).

- [x] **Step 1: Write the failing tests**

```python
# tests/test_ratelimit.py
from phonectl import ratelimit


def test_buckets_for_includes_global_verb_and_high_risk():
    assert set(ratelimit.buckets_for("tap", "low")) == {"global", "tap"}
    assert "high_risk" in ratelimit.buckets_for("tap", "critical")


def test_check_allows_under_limit_and_blocks_at_limit():
    limits = {"tap": 2, "global": 100}
    hist = [{"bucket": "tap", "ts": 0.0}, {"bucket": "global", "ts": 0.0}]
    ok, bucket = ratelimit.check(hist, "tap", "low", limits, now=1.0)
    assert ok is True and bucket is None
    hist += [{"bucket": "tap", "ts": 0.5}, {"bucket": "global", "ts": 0.5}]
    ok, bucket = ratelimit.check(hist, "tap", "low", limits, now=1.0)
    assert ok is False and bucket == "tap"


def test_check_window_expires_old_records():
    limits = {"tap": 1, "global": 100}
    hist = [{"bucket": "tap", "ts": 0.0}, {"bucket": "global", "ts": 0.0}]
    ok, _ = ratelimit.check(hist, "tap", "low", limits, now=120.0, window=60.0)
    assert ok is True                      # the old record aged out of the window


def test_high_risk_bucket_enforced():
    limits = {"high_risk": 1, "global": 100, "tap": 100}
    hist = [{"bucket": "high_risk", "ts": 0.0}, {"bucket": "global", "ts": 0.0},
            {"bucket": "tap", "ts": 0.0}]
    ok, bucket = ratelimit.check(hist, "tap", "critical", limits, now=1.0)
    assert ok is False and bucket == "high_risk"


def test_repeated_hash_detects_stuck_screen():
    assert ratelimit.repeated_hash(["a", "a", "a"]) is True
    assert ratelimit.repeated_hash(["a", "b", "a"]) is False
    assert ratelimit.repeated_hash(["a", "a"]) is False     # below threshold count
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ratelimit.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'phonectl.ratelimit'`).

- [x] **Step 3: Write minimal implementation**

```python
# src/phonectl/ratelimit.py
"""Pure bucketed sliding-window rate limiting + repeated-screen-hash stop
(strategy §8.2). State (the history list) is owned by the caller (runtime persists
it); this module only decides. No I/O, injectable clock."""
from __future__ import annotations


def buckets_for(verb, level) -> list[str]:
    buckets = ["global", verb]
    if level in ("high", "critical"):
        buckets.append("high_risk")
    return buckets


def prune(history, now, window=60.0) -> list[dict]:
    return [r for r in history if now - r["ts"] <= window]


def check(history, verb, level, limits, now, window=60.0):
    recent = prune(history, now, window)
    for bucket in buckets_for(verb, level):
        limit = limits.get(bucket)
        if limit is None:
            continue
        count = sum(1 for r in recent if r["bucket"] == bucket)
        if count >= limit:
            return (False, bucket)
    return (True, None)


def repeated_hash(history_hashes, threshold=3) -> bool:
    if len(history_hashes) < threshold:
        return False
    tail = history_hashes[-threshold:]
    return len(set(tail)) == 1
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ratelimit.py -v`
Expected: PASS (5 tests).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/ratelimit.py tests/test_ratelimit.py
git commit -m "feat: pure bucketed sliding-window rate limit + repeated-hash stop"
```

---

### Task 4: `runtime` policy gate — deny/confirm on classified risk

Insert the policy decision into `run_action` between the confirm-mode check and the act region: classify the
freshly-observed snapshot, and `deny`/`confirm` accordingly through the existing envelope.

**Files:**
- Modify: `src/phonectl/runtime.py` (consult `policy` after `observe`, before `fn`)
- Test: `tests/test_runtime.py` (append)

**Interfaces:**
- After `observer.observe()` inside the lock, `run_action` calls `policy.explain(session.last, verb, target,
  cfg)`:
  - `decision == "deny"` → return `results.err(errors.GuardedActionError(<reason summary>),
    risk_level=..., reasons=..., **base)` (no `fn`, no act). Audited as blocked (Task 6).
  - `decision == "confirm"` and not `yes` → return `results.err(errors.ConfirmationRequiredError(<summary>),
    risk_level=..., reasons=..., **base)`.
  - else proceed to `fn` as today; the success envelope gains `risk_level` + `reasons`.
- The mode-level `confirm` check (Plan 2.1) stays; risk-`confirm` is an *additional* gate that fires even in
  `auto` mode. `--yes` satisfies both.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_runtime.py  (append)
from phonectl import errors as _errors


def _payment_observe(b, s, **kw):
    s.set_snapshot({"hash": "h", "app": {"package": "com.x"},
                    "elements": [{"text": "Confirm payment", "content_desc": "", "password": False}]})


def test_run_action_denies_critical_risk(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    backend = FakeBackend(); sess = FakeSession()
    monkeypatch.setattr(runtime.observer, "observe", _payment_observe)
    acted = []
    env = runtime.run_action("tap", lambda b, s: acted.append(1), {"i": 0},
                             build=lambda cfg: (backend, sess, FakeConn()))
    assert env["ok"] is False
    assert env["error"]["code"] == "guarded_action"
    assert env["risk_level"] == "critical"
    assert acted == []                         # denied before acting


def test_run_action_high_risk_confirm_requires_yes(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    backend = FakeBackend(); sess = FakeSession()
    def observe(b, s, **kw):
        s.set_snapshot({"hash": "h", "app": {"package": "com.x"},
                        "elements": [{"text": "", "content_desc": "Password", "password": True}]})
    monkeypatch.setattr(runtime.observer, "observe", observe)
    env = runtime.run_action("type", lambda b, s: {"hash": "x"}, {"text": "<x>"},
                             build=lambda cfg: (backend, sess, FakeConn()), yes=False)
    assert env["error"]["code"] == "confirmation_required"
    assert env["risk_level"] == "high"
    # with --yes it proceeds despite high risk:
    env2 = runtime.run_action("type", lambda b, s: {"hash": "x"}, {"text": "<x>"},
                              build=lambda cfg: (backend, sess, FakeConn()), yes=True)
    assert env2["ok"] is True and env2["risk_level"] == "high"


def test_run_action_low_risk_success_carries_level(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    backend = FakeBackend(); sess = FakeSession()
    monkeypatch.setattr(runtime.observer, "observe",
                        lambda b, s, **kw: s.set_snapshot({"hash": "h", "app": {"package": "com.x"},
                                                           "elements": [{"text": "Wi-Fi"}]}))
    env = runtime.run_action("tap", lambda b, s: {"hash": "x"}, {"i": 0},
                             build=lambda cfg: (backend, sess, FakeConn()))
    assert env["ok"] is True and env["risk_level"] == "low"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime.py -v`
Expected: FAIL (`run_action` does not classify risk; no `risk_level` on the envelope).

- [x] **Step 3: Write minimal implementation**

In `src/phonectl/runtime.py` add `from phonectl import policy` and, inside the lock after `observer.observe`,
before the dry-run/act branch:

```python
        decision = policy.explain(session.last, verb, target, cfg)
        risk = {"risk_level": decision["risk_level"], "reasons": decision["reasons"]}
        summary = f"{verb} blocked: risk={decision['risk_level']}"
        if decision["decision"] == "deny":
            return results.err(errors.GuardedActionError(summary), **risk, **base)
        if decision["decision"] == "confirm" and not yes:
            return results.err(
                errors.ConfirmationRequiredError(f"{verb} needs confirmation: risk={decision['risk_level']}"),
                user_action="Re-run with --yes to confirm this action.", **risk, **base)
        if mode == "dry-run":
            return results.ok(capability=f"ui.{verb}", provider="adb",
                              data=session.last, dry_run=True, **risk, **base)
        snap = fn(backend, session)
        log(verb, target, snap, request_id=rid, cfg=cfg)
        return results.ok(capability=f"ui.{verb}", provider="adb", data=snap, **risk, **base)
```

Keep the dry-run branch *after* the policy gate so a denied action is reported as denied even in dry-run (an
agent learns the block without executing). The existing Plan-2.1 dry-run test used a benign snapshot, so it
still returns `ok` (low risk).

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runtime.py -v`
Expected: PASS (existing + 3 new).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/runtime.py tests/test_runtime.py
git commit -m "feat: risk policy gate in run_action (deny/confirm with risk_level + reasons)"
```

---

### Task 5: `runtime` rate-limit gate — bucketed limits + persisted history

Add the rate gate: persist a small history of allowed actions in `PHONECTL_HOME/ratelimit.json`, prune to the
window, and block over-limit actions with `errors.RateLimitError`.

**Files:**
- Modify: `src/phonectl/runtime.py` (load/prune/check before act; append after a successful act)
- Test: `tests/test_runtime.py` (append)

**Interfaces:**
- `_rate_path()` → `config_dir() / "ratelimit.json"`; `_load_rate()`/`_save_rate(history)` — tiny JSON I/O in
  `runtime` (so `ratelimit.py` stays pure).
- Inside the lock, after the policy gate and before acting: `history = ratelimit.prune(_load_rate(), now)`;
  `ok, bucket = ratelimit.check(history, verb, level, cfg.get("rate_limits", DEFAULT_LIMITS), now)`. If not
  `ok`, return `results.err(errors.RateLimitError(f"rate limit exceeded for {bucket}"), bucket=bucket,
  **risk, **base)`. After a successful (non-dry-run) act, append one record per `ratelimit.buckets_for(verb,
  level)` with `ts=now` and `_save_rate`. `now` is injectable (`now=time.time`).
- `DEFAULT_LIMITS` mirrors the `rate_limits` config default.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_runtime.py  (append)
import json as _json


def test_run_action_rate_limits_after_bucket_fills(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import config
    config.save({"rate_limits": {"tap": 1, "global": 100}})
    backend = FakeBackend(); sess = FakeSession()
    monkeypatch.setattr(runtime.observer, "observe",
                        lambda b, s, **kw: s.set_snapshot({"hash": "h", "app": {"package": "com.x"},
                                                           "elements": [{"text": "Wi-Fi"}]}))
    build = lambda cfg: (backend, sess, FakeConn())
    clock = [1000.0]
    first = runtime.run_action("tap", lambda b, s: {"hash": "x"}, {"i": 0},
                               build=build, now=lambda: clock[0])
    assert first["ok"] is True
    second = runtime.run_action("tap", lambda b, s: {"hash": "x"}, {"i": 0},
                                build=build, now=lambda: clock[0])
    assert second["ok"] is False
    assert second["error"]["code"] == "rate_limited"
    assert second["bucket"] == "tap"


def test_rate_history_persisted_and_pruned(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import config
    config.save({"rate_limits": {"tap": 1, "global": 100}})
    backend = FakeBackend(); sess = FakeSession()
    monkeypatch.setattr(runtime.observer, "observe",
                        lambda b, s, **kw: s.set_snapshot({"hash": "h", "app": {"package": "com.x"},
                                                           "elements": [{"text": "Wi-Fi"}]}))
    build = lambda cfg: (backend, sess, FakeConn())
    runtime.run_action("tap", lambda b, s: {"hash": "x"}, {"i": 0}, build=build, now=lambda: 1000.0)
    hist = _json.loads((tmp_path / "ratelimit.json").read_text())
    assert any(r["bucket"] == "tap" for r in hist)
    # 120s later the old record is pruned, so the action is allowed again:
    later = runtime.run_action("tap", lambda b, s: {"hash": "x"}, {"i": 0}, build=build, now=lambda: 1120.0)
    assert later["ok"] is True
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime.py -v`
Expected: FAIL (`run_action` has no `now` rate gate; no `ratelimit.json`).

- [x] **Step 3: Write minimal implementation**

In `src/phonectl/runtime.py` add `import json`, `import time`, `from phonectl import ratelimit`, the
`config_dir` import, `DEFAULT_LIMITS`, the `now=time.time` param, the persistence helpers, and the gate:

```python
DEFAULT_LIMITS = {"tap": 120, "type": 30, "swipe": 120, "key": 120,
                  "launch": 20, "high_risk": 1, "global": 180}


def _rate_path():
    from phonectl.config import config_dir
    return config_dir() / "ratelimit.json"


def _load_rate():
    p = _rate_path()
    return json.loads(p.read_text()) if p.exists() else []


def _save_rate(history):
    _rate_path().write_text(json.dumps(history))
```

In the act region (after the policy gate, before `fn`), with `level = risk["risk_level"]`:

```python
        limits = cfg.get("rate_limits", DEFAULT_LIMITS)
        ts = now()
        history = ratelimit.prune(_load_rate(), ts)
        allowed, bucket = ratelimit.check(history, verb, level, limits, ts)
        if not allowed:
            return results.err(errors.RateLimitError(f"rate limit exceeded for {bucket}"),
                               bucket=bucket, **risk, **base)
        if mode == "dry-run":
            return results.ok(..., dry_run=True, **risk, **base)
        snap = fn(backend, session)
        for b in ratelimit.buckets_for(verb, level):
            history.append({"bucket": b, "ts": ts})
        _save_rate(history)
        log(verb, target, snap, request_id=rid, cfg=cfg)
        return results.ok(..., data=snap, **risk, **base)
```

Add `now=time.time` to the `run_action` signature (and thread it into the extracted `_run` helper). Dry-run
does not record against the rate buckets.

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runtime.py -v`
Expected: PASS (existing + 2 new).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/runtime.py tests/test_runtime.py
git commit -m "feat: bucketed rate-limit gate in run_action with persisted sliding-window history"
```

---

### Task 6: Audit blocked actions + `phonectl policy explain` verb

Record policy/rate blocks in the audit log (so denials are traceable, strategy §24) and expose
`policy.explain` as a CLI verb an agent/user can query before acting.

**Files:**
- Modify: `src/phonectl/runtime.py` (audit blocked outcomes)
- Modify: `src/phonectl/cli.py` (add `policy explain`)
- Test: `tests/test_runtime.py`, `tests/test_cli.py` (append)

**Interfaces:**
- On a `deny`/`confirm`/`rate_limited` block, `run_action` calls `log(verb, target, {"app":
  session.last.get("app", {}), "hash": session.last.get("hash", "")}, request_id=rid, cfg=cfg)` with an extra
  `outcome` marker — extend `audit.log_action` to accept `outcome="ok"|"blocked"` (default `"ok"`) and record
  it. (Small additive signature change; default keeps Plan-2.1 callers green.)
- CLI: `phonectl policy explain` observes once (read-only, no act, no kill-switch needed) and prints
  `policy.explain(snap, verb, target, cfg)`; args `--verb` (default `tap`), `--text/--id/--selector/--index`
  reuse `_selector_from_args`. Returns `0`; supports `--json`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_runtime.py  (append)
def test_blocked_action_is_audited(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    backend = FakeBackend(); sess = FakeSession()
    monkeypatch.setattr(runtime.observer, "observe", _payment_observe)
    runtime.run_action("tap", lambda b, s: None, {"i": 0},
                       build=lambda cfg: (backend, sess, FakeConn()))
    import json as _j
    rec = _j.loads((tmp_path / "actions.jsonl").read_text().strip().splitlines()[-1])
    assert rec["outcome"] == "blocked" and rec["verb"] == "tap"
```

```python
# tests/test_cli.py  (append)
def test_policy_explain_reports_decision(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    class PayBackend(FakeBackend):
        def ui_dump(self):
            return ("""<?xml version='1.0'?><hierarchy rotation="0">"""
                    """<node index="0" text="Confirm payment" class="T" clickable="true" """
                    """bounds="[0,0][10,10]"/></hierarchy>""")
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: PayBackend())
    rc = cli.main(["policy", "explain", "--text", "Confirm payment", "--json"])
    out = _json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["risk_level"] == "critical" and out["decision"] == "deny"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime.py tests/test_cli.py -v`
Expected: FAIL (no `outcome` in audit; no `policy` subcommand).

- [x] **Step 3: Write minimal implementation**

Extend `audit.log_action` with `outcome="ok"` (additive kwarg) and write `rec["outcome"] = outcome`. In
`run_action`, on each block path, audit before returning:

```python
        blocked_result = {"app": session.last.get("app", {}), "hash": session.last.get("hash", "")}
        if decision["decision"] == "deny":
            log(verb, target, blocked_result, request_id=rid, cfg=cfg, outcome="blocked")
            return results.err(errors.GuardedActionError(summary), **risk, **base)
        # ...same for the confirm and rate-limited paths...
```

In `cli.py` add the `policy` subcommand group:

```python
def _cmd_policy(args):
    cfg = config.load()
    backend, session, conn = build_runtime(cfg)
    conn.ensure()
    snap = observer.observe(backend, session)
    target = _selector_from_args(args) or {}
    out = policy.explain(snap, args.verb, target, cfg)
    print(json.dumps(out, indent=2) if getattr(args, "json", False) else
          f"phonectl: {args.verb} -> {out['decision']} (risk={out['risk_level']})")
    return 0
```

```python
    # build_parser: register policy explain
    po = sub.add_parser("policy")
    posub = po.add_subparsers(dest="policy_cmd")
    pe = posub.add_parser("explain")
    pe.add_argument("--verb", default="tap")
    pe.add_argument("--text"); pe.add_argument("--id"); pe.add_argument("--selector")
    pe.add_argument("--index", type=int); pe.add_argument("--nth", type=int)
    pe.add_argument("--json", action="store_true")
    pe.set_defaults(func=_cmd_policy)
```

Add `from phonectl import policy` to `cli.py` imports.

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runtime.py tests/test_cli.py -v`
Expected: PASS (existing + 2 new).

- [x] **Step 5: Commit**

```bash
git add src/phonectl/runtime.py src/phonectl/audit.py src/phonectl/cli.py tests/test_runtime.py tests/test_cli.py
git commit -m "feat: audit blocked actions (outcome) + phonectl policy explain verb"
```

---

### Task 7: Docs — risk ledger, policy, rate limits

**Files:**
- Modify: `README.md` (add a "Risk ledger & policy" section)
- Modify: `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md` (note the risk-classifier
  invariant that supersedes the flat guarded-package denylist)

**Interfaces:** none (documentation).

- [x] **Step 1: Run the full suite, then document**

Run: `pytest -v` (expect green across risk, policy, ratelimit, runtime, audit, cli, and all prior tests).

In `README.md`: the risk levels + signal table, the `risk_policy`/`rate_limits`/`guarded_packages` config
keys and defaults, the `guarded_action`/`rate_limited`/`confirmation_required` envelope codes with
`risk_level`/`reasons`/`bucket`, and the `phonectl policy explain` verb. In the design spec, note that
**risk classification + per-level policy now generalizes the guarded-package denylist and the single
rate-limit** (strategy §8, §24), and that all enforcement lives at the `run_action` choke-point.

- [x] **Step 2: Commit**

```bash
git add README.md docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md
git commit -m "docs: risk ledger, policy decisions, bucketed rate limits, policy explain"
```

---

## Dependencies

**Plan 2.2 of the platform roadmap.** Requires **Plan 1.1** (`errors.GuardedActionError`/`RateLimitError`,
`results`), **Plan 1.2** (element metadata the classifier reads), and **Plan 2.1** (`run_action` funnel +
`ConfirmationRequiredError`). It supersedes `2026-06-21-phonectl-safety-completeness.md`. Downstream:

- **Plan 2.3** (MCP) exposes `phone.policy.explain` over `policy.explain` and surfaces the
  `guarded_action`/`rate_limited` envelopes verbatim from its action tools.
- **Phase 3** providers (intents/packages/clipboard) register new verbs into `HIGH_RISK_VERBS` and add
  signals (intent action, install/uninstall) to `risk.classify`.
- **Phase 6** (macros) reads `policy.explain` at the `risk_below` condition and re-checks risk before replaying
  high-risk actions (strategy §23).

## Deferred / out of scope (not in this plan)

- **Per-app rate limits, burst-vs-sustained curves, cooldowns after failed actions** (strategy §8.2 tail) —
  the bucketed sliding-window + repeated-hash stop ship here; finer curves are a later refinement.
- **Durable, cross-process rate state under contention** — `ratelimit.json` is a simple last-writer-wins file;
  the daemon (Phase 5) owns the authoritative single-writer ledger.
- **Intent/notification-category/amount-currency signals** (strategy §24) — added when their providers land
  (Phase 3/4); the classifier's signal set is intentionally extensible.
- **ML/learned risk scoring** — out of scope; the classifier stays conservative, explainable, and
  user-overridable (strategy §24 closing).

## Notes on testability

`risk`, `policy`, and `ratelimit` are pure and fixture-tested with hand-built snapshots and history lists — no
device, no clock. The `run_action` policy + rate gates are tested with a fake `build`, a monkeypatched
`observer.observe` that returns scripted snapshots (benign / payment / password), and an injected `now`, so
deny/confirm/rate-limit/replay paths are exercised deterministically. The rate-history persistence is verified
by reading the real `ratelimit.json` under `tmp_path`. The `policy explain` CLI uses a payment-screen
`FakeBackend` XML fixture. No test sleeps or talks to a device.
