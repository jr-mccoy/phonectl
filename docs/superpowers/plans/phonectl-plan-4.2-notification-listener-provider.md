# phonectl NotificationListenerService Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 4.2 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). Second plan of
Phase 4. Depends on **Plan 1.1** (`errors`/`results`/`capabilities`), **Plan 2.1**
(`runtime.run_action`), **Plan 2.2** (risk classifier), **Plan 3.1** (`ProviderRegistry`), and **Plan
4.1** (the `Transport` seam). Builds on the Plan 3.5 deferral note that 4.2 owns notification semantics.

**Goal:** Make notifications a **first-class provider, not UI-scraping** (strategy §19, §20.3). Add a
`NotificationsProvider` exposing `notifications list/wait/reply/dismiss` with **per-notification reply
capability flags** derived from each notification's actions / `RemoteInput`. The provider prefers the
companion APK's `NotificationListenerService` over the 4.1 `Transport`, and degrades to a **read-only**
Termux:API path (`termux-notification-list`) when only Termux:API is present — listing works, but `wait`
is best-effort and `reply`/`dismiss` are unavailable (capability flags say so). `reply` and `dismiss`
**mutate** and therefore route through `runtime.run_action` for mode/kill-switch/risk gating.

**Architecture:** One new provider module `src/phonectl/providers/notifications.py`
(`NotificationsProvider`). It is constructed with an optional `Transport` (companion) and an optional
`termux` provider (the Plan 3.5 `TermuxApiProvider`, used only for the degraded list path). A pure helper
`notifications.parse_notification(raw) -> dict` normalizes both companion JSON and Termux JSON into one
shape and computes `can_reply`/`can_dismiss`. `cli.build_runtime()` constructs the provider from whatever
is available and prepends it to the registry. CLI verbs `phonectl notifications …` and MCP tools wrap it;
the mutating verbs call `runtime.run_action`.

**Tech Stack:** Python 3 (stdlib only: `json`, `time`, `typing`); `pytest` for tests; no new runtime deps.
Both the companion APK and Termux:API are **runtime-optional**; absent both, the capability is simply
unavailable and the CLI/MCP return a `CapabilityUnavailableError` envelope with a setup hint.

## Global Constraints

- **stdlib-only at runtime.** No third-party deps.
- **Backend isolation.** The provider reaches the companion only via the injected `Transport`, and the
  Termux path only via the injected `TermuxApiProvider` (which itself uses `runner`/`which`). It never
  calls `adb`/`subprocess` directly and never imports `adb_backend`.
- **`ui_parser.py` stays pure** and untouched.
- **Mutating actions are gated.** `reply`/`dismiss` go through `runtime.run_action` so mode
  (`auto/confirm/dry-run`), the `STOP` kill-switch, and the risk policy all apply. Reads (`list`/`wait`)
  do not route through `run_action` (they are non-mutating).
- **Risk classification (Plan 2.2).** `notifications_reply` is a **high-risk** verb (it sends content the
  user can see, into arbitrary apps); add it to `HIGH_RISK_VERBS`. `notifications_dismiss` is low/medium.
- **Injectable seams.** `NotificationsProvider(transport=None, termux=None)`. Tests pass a
  `LoopbackTransport` and/or a fake termux. Isolate state via `monkeypatch.setenv("PHONECTL_HOME", …)`.
- **Structured-result invariant (Plan 1.1):** CLI `--json` and MCP tools return `results.ok/err`.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **New capability keys** in `CAPABILITY_KEYS`: `observe_notifications`, `notifications_wait`,
  `notifications_reply`, `notifications_dismiss`.
- **Normalized notification shape** (`parse_notification` output and the provider's `list()` items):
  `{"key": str, "package": str, "title": str, "text": str, "category": str | None, "post_time": int,
  "actions": [str], "can_reply": bool, "can_dismiss": bool}`. The opaque `key` is the
  `StatusBarNotification` key used by `reply`/`dismiss`.
- **`can_reply`** is `True` only when an action with a `RemoteInput` exists (companion sets
  `"remote_input": true` on that action). The Termux path always reports `can_reply=False`,
  `can_dismiss=False`.
- **Provider source precedence:** companion `Transport` first (full features), else `TermuxApiProvider`
  (list-only). `capabilities()` reflects which source is active.

---

### Task 1: Capability keys + `parse_notification` (pure) + provider skeleton

**Files:**
- Modify: `src/phonectl/capabilities.py`
- Create: `src/phonectl/providers/notifications.py`
- Test: `tests/test_capabilities.py` (append), `tests/test_providers_notifications.py` (create)

**Interfaces:**
- New keys: `observe_notifications`, `notifications_wait`, `notifications_reply`,
  `notifications_dismiss`.
- `notifications.parse_notification(raw: dict, *, source: str) -> dict` — pure normalizer. `source` ∈
  `{"companion", "termux"}`. Computes `can_reply` from `raw["actions"]` (companion) and forces
  `can_reply=can_dismiss=False` for `source == "termux"`.
- `NotificationsProvider(transport=None, termux=None, *, timeout=2.0)`.
- `NotificationsProvider.is_available() -> bool` — `True` if a reachable companion **or** an available
  termux.
- `NotificationsProvider.capabilities() -> dict` — companion → all four keys `True`; termux-only →
  `observe_notifications=True` only; neither → all `False`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_capabilities.py
def test_notification_capability_keys_exist():
    from phonectl import capabilities
    for key in ("observe_notifications", "notifications_wait",
                "notifications_reply", "notifications_dismiss"):
        assert key in capabilities.CAPABILITY_KEYS


# tests/test_providers_notifications.py (new file)
import pytest
from phonectl.providers import notifications as nmod
from phonectl.providers.notifications import NotificationsProvider
from phonectl.providers.transport import LoopbackTransport

COMPANION_RAW = {
    "key": "0|com.msg|42|tag|10123", "package": "com.msg",
    "title": "Alice", "text": "see you at 6?", "category": "msg", "post_time": 1718900000000,
    "actions": [{"title": "Reply", "remote_input": True}, {"title": "Mark read"}],
}


def test_parse_notification_companion_sets_can_reply():
    n = nmod.parse_notification(COMPANION_RAW, source="companion")
    assert n["key"] == "0|com.msg|42|tag|10123"
    assert n["can_reply"] is True
    assert n["can_dismiss"] is True
    assert "Reply" in n["actions"]


def test_parse_notification_termux_is_read_only():
    raw = {"id": 7, "packageName": "com.msg", "title": "Alice", "content": "hi"}
    n = nmod.parse_notification(raw, source="termux")
    assert n["can_reply"] is False
    assert n["can_dismiss"] is False
    assert n["package"] == "com.msg"


def test_capabilities_full_with_companion():
    p = NotificationsProvider(transport=LoopbackTransport({}))
    caps = p.capabilities()
    assert caps["notifications_reply"] is True
    assert caps["notifications_dismiss"] is True


def test_capabilities_listonly_with_termux():
    class FakeTermux:
        def is_available(self): return True
    p = NotificationsProvider(transport=None, termux=FakeTermux())
    caps = p.capabilities()
    assert caps["observe_notifications"] is True
    assert caps["notifications_reply"] is False


def test_capabilities_empty_when_nothing_available():
    p = NotificationsProvider(transport=LoopbackTransport({}, available=False), termux=None)
    assert all(v is False for v in p.capabilities().values())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_capabilities.py tests/test_providers_notifications.py -v`
Expected: FAIL (`ModuleNotFoundError`; missing keys).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/capabilities.py — append to CAPABILITY_KEYS
    # Phase 4.2 additions (NotificationListenerService companion)
    "observe_notifications",
    "notifications_wait",
    "notifications_reply",
    "notifications_dismiss",
```

```python
# src/phonectl/providers/notifications.py
"""Notification provider — companion NotificationListenerService or read-only Termux:API."""
from __future__ import annotations

from phonectl import capabilities as caps_mod
from phonectl import errors
from phonectl.providers.transport import next_request_id


def parse_notification(raw: dict, *, source: str) -> dict:
    if source == "termux":
        return {
            "key": str(raw.get("id", "")),
            "package": raw.get("packageName") or raw.get("package", ""),
            "title": raw.get("title", "") or "",
            "text": raw.get("content") or raw.get("text", "") or "",
            "category": raw.get("category"),
            "post_time": int(raw.get("when", 0) or 0),
            "actions": [],
            "can_reply": False,
            "can_dismiss": False,
        }
    actions = raw.get("actions", []) or []
    can_reply = any(a.get("remote_input") for a in actions)
    return {
        "key": raw.get("key", ""),
        "package": raw.get("package", ""),
        "title": raw.get("title", "") or "",
        "text": raw.get("text", "") or "",
        "category": raw.get("category"),
        "post_time": int(raw.get("post_time", 0) or 0),
        "actions": [a.get("title", "") for a in actions],
        "can_reply": bool(can_reply),
        "can_dismiss": True,
    }


class NotificationsProvider:
    def __init__(self, transport=None, termux=None, *, timeout: float = 2.0) -> None:
        self._t = transport
        self._termux = termux
        self._timeout = timeout

    def _companion_ok(self) -> bool:
        try:
            return self._t is not None and bool(self._t.ping())
        except Exception:  # noqa: BLE001
            return False

    def _termux_ok(self) -> bool:
        return self._termux is not None and bool(self._termux.is_available())

    def is_available(self) -> bool:
        return self._companion_ok() or self._termux_ok()

    def capabilities(self) -> dict:
        if self._companion_ok():
            return caps_mod.make(observe_notifications=True, notifications_wait=True,
                                 notifications_reply=True, notifications_dismiss=True)
        if self._termux_ok():
            return caps_mod.make(observe_notifications=True)
        return caps_mod.make()

    def _call(self, method: str, params: dict | None = None) -> dict:
        rid = next_request_id()
        resp = self._t.request(method, params or {}, request_id=rid, timeout=self._timeout)
        if resp.get("request_id") != rid:
            raise errors.ObserveError("stale companion response for notifications")
        if not resp.get("ok"):
            raise errors.ActionError(resp.get("error", {}).get("message", "companion error"))
        return resp.get("data", {})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_capabilities.py tests/test_providers_notifications.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/capabilities.py src/phonectl/providers/notifications.py \
        tests/test_capabilities.py tests/test_providers_notifications.py
git commit -m "feat: NotificationsProvider skeleton + pure parse_notification + capability keys"
```

---

### Task 2: `list()` notifications (companion + Termux fallback)

**Files:**
- Modify: `src/phonectl/providers/notifications.py`
- Test: `tests/test_providers_notifications.py` (append)

**Interfaces:**
- `list(package: str | None = None) -> list[dict]` — companion: `self._call("notifications_list", {})`,
  normalizing each via `parse_notification(..., source="companion")`. Termux fallback:
  `self._termux.notifications_list()` (a new thin method on `TermuxApiProvider` calling
  `termux-notification-list`), normalized with `source="termux"`. Optional `package` filters by package.
- New on `TermuxApiProvider`: `notifications_list() -> list[dict]` — runs `termux-notification-list`,
  `json.loads` the array (returns `[]` on empty/unparseable). (Added here because Plan 3.5 deferred it.)

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_providers_notifications.py
import json as _json


def test_list_companion_normalizes_items():
    def handler(_p):
        return {"notifications": [COMPANION_RAW]}
    p = NotificationsProvider(transport=LoopbackTransport({"notifications_list": handler}))
    items = p.list()
    assert len(items) == 1
    assert items[0]["can_reply"] is True
    assert items[0]["package"] == "com.msg"


def test_list_filters_by_package():
    raw2 = dict(COMPANION_RAW, key="k2", package="com.other")
    def handler(_p):
        return {"notifications": [COMPANION_RAW, raw2]}
    p = NotificationsProvider(transport=LoopbackTransport({"notifications_list": handler}))
    assert [n["package"] for n in p.list(package="com.msg")] == ["com.msg"]


def test_list_termux_fallback_is_read_only():
    class FakeTermux:
        def is_available(self): return True
        def notifications_list(self):
            return [{"id": 1, "packageName": "com.msg", "title": "A", "content": "hi"}]
    p = NotificationsProvider(transport=None, termux=FakeTermux())
    items = p.list()
    assert items[0]["can_reply"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_providers_notifications.py -v -k "list"`
Expected: FAIL (`AttributeError: ... 'list'`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/notifications.py — add to NotificationsProvider

def list(self, package: str | None = None) -> list:
    if self._companion_ok():
        data = self._call("notifications_list", {})
        items = [parse_notification(r, source="companion")
                 for r in data.get("notifications", [])]
    elif self._termux_ok():
        items = [parse_notification(r, source="termux")
                 for r in self._termux.notifications_list()]
    else:
        raise errors.CapabilityUnavailableError("no notification source available")
    if package is not None:
        items = [n for n in items if n["package"] == package]
    return items
```

```python
# src/phonectl/providers/termux.py — add to TermuxApiProvider
def notifications_list(self) -> list:
    import json as _json
    raw = self._run("termux-notification-list").strip()
    if not raw:
        return []
    try:
        data = _json.loads(raw)
    except _json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_providers_notifications.py tests/test_providers_termux.py -v -k "list or notifications"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/notifications.py src/phonectl/providers/termux.py \
        tests/test_providers_notifications.py
git commit -m "feat: NotificationsProvider.list (companion + Termux read-only) and TermuxApiProvider.notifications_list"
```

---

### Task 3: `wait(predicate, timeout)`

**Files:**
- Modify: `src/phonectl/providers/notifications.py`
- Test: `tests/test_providers_notifications.py` (append)

**Interfaces:**
- `wait(*, package=None, title_contains=None, text_contains=None, timeout=30.0, poll=1.0,
  _clock=time.monotonic, _sleep=time.sleep) -> dict | None` — polls `list()` until a notification matches
  the predicate or the timeout elapses; returns the first match or `None`. Uses a **monotonic deadline**
  (injectable `_clock`/`_sleep` for deterministic tests). When a companion is present, prefers the
  companion's blocking `notifications_wait` (single round trip) but the polling form is the portable
  default and the Termux path's only option.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_providers_notifications.py

def test_wait_returns_first_match(monkeypatch):
    calls = {"n": 0}
    def handler(_p):
        calls["n"] += 1
        if calls["n"] < 3:
            return {"notifications": []}
        return {"notifications": [COMPANION_RAW]}
    p = NotificationsProvider(transport=LoopbackTransport({"notifications_list": handler}))
    t = {"now": 0.0}
    out = p.wait(package="com.msg", timeout=10.0, poll=1.0,
                 _clock=lambda: t["now"], _sleep=lambda s: t.__setitem__("now", t["now"] + s))
    assert out is not None and out["package"] == "com.msg"


def test_wait_times_out_returns_none():
    def handler(_p):
        return {"notifications": []}
    p = NotificationsProvider(transport=LoopbackTransport({"notifications_list": handler}))
    t = {"now": 0.0}
    out = p.wait(text_contains="never", timeout=3.0, poll=1.0,
                 _clock=lambda: t["now"], _sleep=lambda s: t.__setitem__("now", t["now"] + s))
    assert out is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_providers_notifications.py -v -k "wait"`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/notifications.py
import time


def _matches(n, package, title_contains, text_contains) -> bool:
    if package is not None and n["package"] != package:
        return False
    if title_contains is not None and title_contains not in n["title"]:
        return False
    if text_contains is not None and text_contains not in n["text"]:
        return False
    return True


# method on NotificationsProvider:
def wait(self, *, package=None, title_contains=None, text_contains=None,
         timeout: float = 30.0, poll: float = 1.0,
         _clock=time.monotonic, _sleep=time.sleep):
    deadline = _clock() + timeout
    while True:
        for n in self.list():
            if _matches(n, package, title_contains, text_contains):
                return n
        if _clock() >= deadline:
            return None
        _sleep(poll)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_providers_notifications.py -v -k "wait"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/notifications.py tests/test_providers_notifications.py
git commit -m "feat: NotificationsProvider.wait with monotonic deadline + injectable clock/sleep"
```

---

### Task 4: `reply()` + `dismiss()` (mutating provider methods)

**Files:**
- Modify: `src/phonectl/providers/notifications.py`
- Test: `tests/test_providers_notifications.py` (append)

**Interfaces:**
- `reply(key: str, text: str) -> dict` — `self._call("notifications_reply", {"key": key, "text": text})`.
  Raises `errors.CapabilityUnavailableError` when no companion (Termux cannot reply).
- `dismiss(key: str) -> dict` — `self._call("notifications_dismiss", {"key": key})`. Same
  companion-required guard.

These are the raw provider methods; the **CLI/MCP layer** wraps them in `runtime.run_action` (Task 6) so
mode/kill-switch/risk gating applies.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_providers_notifications.py

def test_reply_calls_companion():
    seen = {}
    def handler(p):
        seen.update(p); return {"sent": True}
    p = NotificationsProvider(transport=LoopbackTransport({"notifications_reply": handler}))
    out = p.reply("0|com.msg|42|tag|10123", "on my way")
    assert out["sent"] is True
    assert seen == {"key": "0|com.msg|42|tag|10123", "text": "on my way"}


def test_reply_unavailable_without_companion():
    class FakeTermux:
        def is_available(self): return True
        def notifications_list(self): return []
    p = NotificationsProvider(transport=None, termux=FakeTermux())
    with pytest.raises(Exception):
        p.reply("k", "hi")


def test_dismiss_calls_companion():
    seen = {}
    def handler(p):
        seen.update(p); return {"dismissed": True}
    p = NotificationsProvider(transport=LoopbackTransport({"notifications_dismiss": handler}))
    assert p.dismiss("k1")["dismissed"] is True
    assert seen == {"key": "k1"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_providers_notifications.py -v -k "reply or dismiss"`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/notifications.py — add to NotificationsProvider

def reply(self, key: str, text: str) -> dict:
    if not self._companion_ok():
        raise errors.CapabilityUnavailableError(
            "notification reply requires the companion APK (Termux:API cannot reply)")
    return self._call("notifications_reply", {"key": key, "text": text})

def dismiss(self, key: str) -> dict:
    if not self._companion_ok():
        raise errors.CapabilityUnavailableError(
            "notification dismiss requires the companion APK")
    return self._call("notifications_dismiss", {"key": key})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_providers_notifications.py -v -k "reply or dismiss"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/notifications.py tests/test_providers_notifications.py
git commit -m "feat: NotificationsProvider.reply/dismiss via RemoteInput (companion-required)"
```

---

### Task 5: Risk classification + `build_runtime` wiring

**Files:**
- Modify: `src/phonectl/risk.py` (add `notifications_reply` to `HIGH_RISK_VERBS`)
- Modify: `src/phonectl/cli.py` (`_make_notifications_provider` + prepend to registry)
- Test: `tests/test_risk.py` (append), `tests/test_cli.py` (append)

**Interfaces:**
- `risk.HIGH_RISK_VERBS` gains `notifications_reply` (sends visible content to apps). `notifications_dismiss`
  stays default (low/medium) — it removes a notification, not data exfiltration.
- `_make_notifications_provider() -> NotificationsProvider | None` — builds from the active accessibility
  transport (Plan 4.1 / 4.3) and the termux provider; returns `None` when neither source is available.
- `build_runtime` prepends it so `observe_notifications` resolves to it.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_risk.py
def test_notifications_reply_is_high_risk():
    from phonectl import risk
    assert "notifications_reply" in risk.HIGH_RISK_VERBS


# Append to tests/test_cli.py
def test_build_runtime_includes_notifications_when_available(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl import cli, config
    from phonectl.providers.notifications import NotificationsProvider
    from phonectl.providers.transport import LoopbackTransport
    np = NotificationsProvider(transport=LoopbackTransport({}))
    monkeypatch.setattr(cli, "_make_notifications_provider", lambda: np)
    monkeypatch.setattr(cli, "_make_accessibility_provider", lambda: None)
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: None)
    cfg = config.load()
    registry, _, _ = cli.build_runtime(cfg)
    assert registry.for_capability("observe_notifications") is np
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_risk.py tests/test_cli.py -v -k "notifications"`
Expected: FAIL (key not in set; `_make_notifications_provider` missing).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/risk.py — add to HIGH_RISK_VERBS
HIGH_RISK_VERBS = frozenset({
    # ... existing ...
    "notifications_reply",
})
```

```python
# src/phonectl/cli.py
from phonectl.providers.notifications import NotificationsProvider


def _make_notifications_provider():
    # Companion transport comes from Plan 4.3; termux from Plan 3.5.
    termux = _make_termux_provider()
    transport = None  # Plan 4.3 supplies the SocketTransport; None here keeps builds Termux-only.
    p = NotificationsProvider(transport=transport, termux=termux)
    return p if p.is_available() else None


def build_runtime(cfg, backend=None):
    adb = backend or _make_backend(cfg)
    providers = [p for p in [
        _make_accessibility_provider(),
        _make_notifications_provider(),
        _make_termux_provider(),
        adb,
    ] if p is not None]
    registry = ProviderRegistry(providers)
    session = Session()
    conn = Connection(registry, cfg)
    return registry, session, conn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_risk.py tests/test_cli.py -v`
Expected: PASS (new tests + all existing — provider helpers default to `None`/Termux-only).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/risk.py src/phonectl/cli.py tests/test_risk.py tests/test_cli.py
git commit -m "feat: classify notifications_reply high-risk; wire NotificationsProvider into build_runtime"
```

---

### Task 6: CLI verbs `phonectl notifications list|wait|reply|dismiss`

**Files:**
- Modify: `src/phonectl/cli.py`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- `phonectl notifications list [--package P] [--json]` — read-only; `results.ok(capability=
  "notifications.list", data=items)`.
- `phonectl notifications wait [--package P] [--title-contains S] [--text-contains S] [--timeout N]
  [--json]` — read-only; returns the match or a `not_found`-style `results.ok(data=None)`.
- `phonectl notifications reply KEY TEXT [--yes] [--json]` — **mutating**; calls
  `runtime.run_action("notifications_reply", lambda b, s: provider.reply(key, text), target=key, ...)`
  so mode/kill-switch/risk gating applies; high-risk → confirm unless `--yes`/`auto`.
- `phonectl notifications dismiss KEY [--yes] [--json]` — **mutating** via `run_action`.
- All resolve the provider via `backend.for_capability("observe_notifications")`; unavailable →
  `results.err(CapabilityUnavailableError, user_action="Install the phonectl companion APK or Termux:API…")`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_cli.py
def test_notifications_list_unavailable(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_accessibility_provider", lambda: None)
    monkeypatch.setattr(cli, "_make_notifications_provider", lambda: None)
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: None)
    rc = cli.main(["notifications", "list", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc != 0 and out["ok"] is False
    assert out["error"]["code"] == "capability_unavailable"


def test_notifications_list_ok(tmp_path, monkeypatch, capsys):
    from phonectl.providers.notifications import NotificationsProvider
    from phonectl.providers.transport import LoopbackTransport
    raw = {"key": "k", "package": "com.msg", "title": "Alice", "text": "hi",
           "category": "msg", "post_time": 1, "actions": [{"title": "Reply", "remote_input": True}]}
    np = NotificationsProvider(transport=LoopbackTransport(
        {"notifications_list": lambda p: {"notifications": [raw]}}))
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr(cli, "_make_accessibility_provider", lambda: None)
    monkeypatch.setattr(cli, "_make_notifications_provider", lambda: np)
    monkeypatch.setattr(cli, "_make_termux_provider", lambda: None)
    rc = cli.main(["notifications", "list", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True
    assert out["data"][0]["can_reply"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v -k "notifications_list"`
Expected: FAIL (subparsers not added).

- [ ] **Step 3: Write minimal implementation**

Add a `notifications` subcommand group in `build_parser()` with `list/wait/reply/dismiss`, and handlers
that resolve `backend.for_capability("observe_notifications")`, emit `results.err(...)` when `None`, and
route `reply`/`dismiss` through `runtime.run_action` (reuse the existing `_do_action`/`run_action` wiring
used by `tap`/`type`). Read verbs print `results.ok(...)` directly.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py tests/test_cli.py
git commit -m "feat: CLI phonectl notifications list|wait|reply|dismiss (reply/dismiss via run_action)"
```

---

### Task 7: MCP tools

**Files:**
- Modify: `src/phonectl/mcp_server.py`
- Test: `tests/test_mcp_server.py` (append)

**Interfaces:**
- `phone_notifications_list(package=None)` → `results.ok/err` envelope of normalized items.
- `phone_notifications_wait(package=None, title_contains=None, text_contains=None, timeout=30)`.
- `phone_notifications_reply(key, text)` — routes through `run_action` (high-risk; honors policy).
- `phone_notifications_dismiss(key)` — routes through `run_action`.
- Each item exposes `can_reply`/`can_dismiss` so the agent checks the flag before attempting a reply
  (strategy §20.3 "capability flags for whether a specific notification exposes a reply action").

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_mcp_server.py
def test_phone_notifications_list_tool_registered():
    from phonectl import mcp_server
    assert "phone_notifications_list" in mcp_server.TOOLS


def test_phone_notifications_reply_routes_through_policy(monkeypatch, tmp_path):
    # call_tool for reply returns an envelope and never bypasses run_action
    from phonectl import mcp_server
    env = mcp_server.call_tool("phone_notifications_reply",
                               {"key": "k", "text": "hi"}, _build=_fake_build_with_companion(tmp_path))
    assert "ok" in env
```

(Model `_fake_build_with_companion` on the existing MCP test doubles; if the existing suite injects the
runtime differently, follow that pattern instead.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_server.py -v -k "notifications"`
Expected: FAIL (tools not registered).

- [ ] **Step 3: Write minimal implementation**

Register the four tools in `TOOLS` and `call_tool`, mirroring the existing clipboard/intent MCP tools from
Plan 3.2 (read tools return `results.ok`; `reply`/`dismiss` go through `run_action`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mcp_server.py -v -k "notifications"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: MCP notification tools list/wait/reply/dismiss with reply capability flags"
```

---

### Task 8: Docs

**Files:**
- Modify: `README.md`
- Modify: `android/accessibility-companion/SPEC.md` (add the NotificationListenerService method set)
- Modify: `docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md`

- [ ] **Step 1: Update docs**

In `README.md`, add a **Notifications** section: the four verbs, the `can_reply`/`can_dismiss` flags, the
companion-vs-Termux feature matrix (Termux:API = list-only), and that `reply` is high-risk and
policy-gated. In the companion SPEC, document the transport methods `notifications_list`,
`notifications_wait`, `notifications_reply` (RemoteInput), `notifications_dismiss`, and the
`NotificationListenerService` permission/grant flow. In the design spec, note notifications as a
first-class provider capability rather than UI scraping.

Run the full suite before committing:

```bash
pytest -v
```

- [ ] **Step 2: Commit**

```bash
git add README.md android/accessibility-companion/SPEC.md \
        docs/superpowers/specs/2026-06-20-phonectl-adb-bridge-design.md
git commit -m "docs: notification provider — verbs, reply capability flags, companion vs Termux matrix"
```

---

## Dependencies

**Requires:** Plan 1.1 (errors/results/capabilities), Plan 2.1 (`run_action` for mutating verbs), Plan
2.2 (risk — `HIGH_RISK_VERBS`), Plan 3.1 (`ProviderRegistry`), Plan 4.1 (`Transport` seam; reuse the
companion). Optional: Plan 3.5 (`TermuxApiProvider`) for the read-only fallback.
**Enables:** Phase 5 (the daemon subscribes to notification events and drives notification-triggered
macros), Phase 6 (notification triggers/conditions/actions in the macro runtime).

## Deferred / out of scope

- **Notification event subscriptions / push** — Plan 4.1's `poll_events` already carries
  `type="notification"`; continuous fanout is a Phase 5 daemon concern. `wait` here is poll-based.
- **Rich notification content** (big-text, media, progress, conversation styles) — only title/text and
  reply actions are normalized; richer extraction can be added when a use case needs it.
- **Per-app reply formatting / multiple RemoteInput fields** — the first reply `RemoteInput` is used;
  multi-field replies are deferred.
- **Termux:API `termux-notification` (posting)** — this plan reads/replies/dismisses; posting our own
  notifications belongs to Plan 4.3's trust UX / the daemon's status surface.

## Notes on testability

No device or APK is needed. `LoopbackTransport` plays the `NotificationListenerService`; a fake termux
provides the read-only path. `parse_notification` is **pure** and covers both source shapes plus the
`can_reply` derivation. `wait` uses an injectable monotonic clock/sleep so timeout and match-on-Nth-poll
are deterministic. The mutating verbs are tested through `runtime.run_action` so the mode/kill-switch/risk
gating is exercised exactly as for `tap`/`type`. The capability-unavailable path (no companion, no termux)
is asserted at both the provider and the CLI/MCP layers.
