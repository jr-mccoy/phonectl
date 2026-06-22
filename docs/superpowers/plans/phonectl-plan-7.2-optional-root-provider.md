# phonectl Optional Root Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 7.2 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). Second plan of
Phase 7. Adds a **strongly-separated, opt-in root provider** for users who explicitly choose full control
via `su` (strategy §13.2 "Root provider": *not required for core value, strongly separated and clearly
labeled*). Depends on **Plan 1.1** (`capabilities`/`results`/`errors`), **Plan 3.1** (`ProviderRegistry`/
`build_runtime`), **Plan 2.2** (`risk` — root ops default to **critical**), and reuses the **same
privileged capability keys** introduced in **Plan 7.1**. It mirrors the Shizuku provider's shape but is
held to a **higher bar of separation, disclosure, and default-deny**.

**Goal:** let an opt-in user run privileged operations via `su -c` when neither ADB-no-root nor Shizuku
suffices, **without** making root a path the platform ever reaches by default or by accident. Root is the
most dangerous provider; this plan's design center is *containment*: a separate config flag distinct from
Shizuku's, default-critical risk for every root op, an explicit per-session arm step, and loud
documentation. This plan ships the **Python-side provider seam** only (no native code; `su` is the host
binary).

**Architecture:** a new `src/phonectl/providers/root.py` (`RootProvider`) that talks to the device **only**
through an injectable `su_runner` (default: `subprocess.run(["su", "-c", cmd])`; tests inject a fake). It
reports privileged capabilities **only when `su` is present, a root probe succeeds, AND
`enable_root_provider` is explicitly true**. `cli.build_runtime` registers it **last among privileged
providers** (Shizuku is preferred when both exist, since Shizuku is the safer privilege) and **only** under
the explicit flag. Every root verb routes through `runtime.run_action` and is classified **critical** by
`risk`, so it requires explicit one-time approval by default.

**Tech Stack:** Python 3 (stdlib only: `subprocess`, `shutil`); `pytest`. No new runtime dep. No native
code (the `su` binary is host-provided).

## Global Constraints

- **stdlib-only at runtime.** `subprocess`, `shutil` — all stdlib.
- **Backend isolation.** Only `root.py` knows about `su`; it runs everything through the injected
  `su_runner`. No other module references `su`.
- **Default-deny, opt-in, separated.** `RootProvider.is_available()` is false unless `su` is present **and**
  a probe succeeds; `capabilities()` is all-false unless available **and** `enable_root_provider` is true.
  The flag is **distinct** from `enable_shizuku` — enabling Shizuku never enables root. Default builds and
  tests are byte-for-byte unchanged.
- **Critical by default.** Every root verb is classified **critical** by `risk` (2.2) ⇒ deny / explicit
  one-time human approval. The provider never lowers the risk floor.
- **One policy choke-point.** Root verbs go through `runtime.run_action`; no bypass.
- **Structured-result invariant.** Unavailable/disabled ⇒ `errors.CapabilityUnavailableError` with an
  actionable `user_action` and an explicit warning that root is the most dangerous provider.
- **Per-session arm.** Beyond the persistent flag, the provider exposes an explicit
  `arm()`/`is_armed` gate so an automated context cannot silently use root even with the flag set
  (belt-and-suspenders containment).
- **Injectable seams.** `RootProvider(*, su_runner=…, which=shutil.which, enabled=…)`. `PHONECTL_HOME`
  isolation.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **Reuses** the Plan-7.1 privileged capability keys (`privileged_shell`/`privileged_pm`/
  `privileged_settings`/`privileged_input`); **adds none.**
- **Config key added:** `enable_root_provider` (bool, default `false`) — distinct from `enable_shizuku`.
- **Risk:** every `RootProvider` verb is mapped to **critical** (a `ROOT_VERBS` set folded into the 2.2
  `CRITICAL_VERBS`), regardless of the underlying command.
- **Arm gate:** `RootProvider.arm()` sets an in-process `_armed` flag; privileged calls require it (raise
  `errors.GuardedActionError` if not armed) — the persistent flag *permits*, the arm *enables*.

---

### Task 1: `RootProvider` discovery + default-deny `capabilities()`

**Files:**
- Create: `src/phonectl/providers/root.py`
- Test: `tests/test_root_provider.py`

**Interfaces:**
- `RootProvider(*, su_runner=None, which=shutil.which, enabled=False)`.
- `is_available() -> bool` — `su` on PATH **and** a probe (`su -c id` returncode 0).
- `capabilities() -> dict` — all-false unless `is_available()` **and** `self._enabled`; then the four
  privileged keys + `write_secure_settings` = True.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_root_provider.py
from phonectl.providers.root import RootProvider


class FakeSu:
    def __init__(self, ok=True, out="uid=0(root)\n"):
        self.ok, self.out, self.calls = ok, out, []

    def __call__(self, argv):
        self.calls.append(argv)
        class R: pass
        r = R(); r.stdout = self.out; r.stderr = ""; r.returncode = 0 if self.ok else 1
        return r


def test_disabled_reports_nothing_even_if_su_present():
    p = RootProvider(su_runner=FakeSu(), which=lambda n: "/sbin/su", enabled=False)
    assert p.is_available() is True
    assert p.capabilities()["privileged_shell"] is False  # disabled by flag


def test_enabled_and_available_reports_caps():
    p = RootProvider(su_runner=FakeSu(ok=True), which=lambda n: "/sbin/su", enabled=True)
    c = p.capabilities()
    assert c["privileged_shell"] and c["privileged_pm"] and c["write_secure_settings"]


def test_no_su_binary_is_unavailable():
    p = RootProvider(su_runner=FakeSu(), which=lambda n: None, enabled=True)
    assert p.is_available() is False
    assert p.capabilities()["privileged_shell"] is False


def test_probe_failure_is_unavailable():
    p = RootProvider(su_runner=FakeSu(ok=False), which=lambda n: "/sbin/su", enabled=True)
    assert p.is_available() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_root_provider.py -v`
Expected: FAIL (`ModuleNotFoundError: phonectl.providers.root`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/root.py
"""Optional, strongly-separated root provider — opt-in, default-deny, critical-risk."""
from __future__ import annotations

import shutil
import subprocess

from phonectl import capabilities as caps_mod


def _default_su(argv):
    return subprocess.run(["su", "-c", " ".join(argv)], capture_output=True, text=True)


class RootProvider:
    def __init__(self, *, su_runner=None, which=shutil.which, enabled=False) -> None:
        self._su = su_runner or _default_su
        self._which = which
        self._enabled = enabled
        self._armed = False

    def is_available(self) -> bool:
        if not self._which("su"):
            return False
        try:
            return getattr(self._su(["id"]), "returncode", 1) == 0
        except Exception:
            return False

    def capabilities(self) -> dict:
        if not (self._enabled and self.is_available()):
            return caps_mod.make()
        return caps_mod.make(
            privileged_shell=True, privileged_pm=True,
            privileged_settings=True, privileged_input=True,
            write_secure_settings=True,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_root_provider.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/root.py tests/test_root_provider.py
git commit -m "feat: optional root provider discovery with default-deny capabilities (opt-in flag)"
```

---

### Task 2: Arm gate + privileged shell (critical-risk)

**Files:**
- Modify: `src/phonectl/providers/root.py`, `src/phonectl/risk.py`
- Test: `tests/test_root_provider.py` (append), `tests/test_risk.py` (append)

**Interfaces:**
- `RootProvider.arm()` / `is_armed` — the per-session enable; `disarm()` clears it.
- `RootProvider.privileged_shell(cmd) -> str` — requires `_enabled`, `is_available()`, **and** `_armed`
  (else `errors.GuardedActionError`); runs via `su_runner`; non-zero ⇒ `errors.PhonectlError`.
- `risk`: a `ROOT_VERBS` set (every root op) folded into `CRITICAL_VERBS`; add a signal so any action whose
  resolved provider is `RootProvider` classifies **critical**.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_root_provider.py — append
import pytest
from phonectl import errors


def _armed():
    p = RootProvider(su_runner=FakeSu(ok=True), which=lambda n: "/sbin/su", enabled=True)
    p.arm()
    return p


def test_privileged_shell_requires_arm():
    p = RootProvider(su_runner=FakeSu(ok=True), which=lambda n: "/sbin/su", enabled=True)
    with pytest.raises(errors.GuardedActionError):
        p.privileged_shell("id")  # not armed
    p.arm()
    assert "root" in p.privileged_shell("id")


def test_privileged_shell_requires_enabled():
    p = RootProvider(su_runner=FakeSu(ok=True), which=lambda n: "/sbin/su", enabled=False)
    p.arm()
    with pytest.raises(errors.CapabilityUnavailableError):
        p.privileged_shell("id")
```

```python
# tests/test_risk.py — append
def test_root_verbs_are_critical():
    from phonectl import risk
    assert risk.ROOT_VERBS <= risk.CRITICAL_VERBS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_root_provider.py tests/test_risk.py -v -k "arm or root or privileged"`
Expected: FAIL (`AttributeError: arm` / `ROOT_VERBS`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/root.py — append
from phonectl import errors


class RootProvider:  # methods added to the class above
    def arm(self):
        self._armed = True

    def disarm(self):
        self._armed = False

    @property
    def is_armed(self):
        return self._armed

    def _run(self, argv):
        if not (self._enabled and self.is_available()):
            raise errors.CapabilityUnavailableError(
                "root provider is disabled; set enable_root_provider=true ONLY if you understand "
                "that root is the most dangerous provider (see docs/root.md).")
        if not self._armed:
            raise errors.GuardedActionError(
                "root provider is not armed for this session; call arm() / `phonectl root arm` first.")
        res = self._su(argv)
        if getattr(res, "returncode", 1) != 0:
            raise errors.PhonectlError(f"root command failed: {' '.join(argv)}")
        return getattr(res, "stdout", "")

    def privileged_shell(self, cmd):
        return self._run(cmd.split() if isinstance(cmd, str) else list(cmd))
```

```python
# src/phonectl/risk.py — append
ROOT_VERBS = {"root_shell", "root_pm", "root_settings", "root_input"}
CRITICAL_VERBS = CRITICAL_VERBS | ROOT_VERBS
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_root_provider.py tests/test_risk.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/root.py src/phonectl/risk.py tests/test_root_provider.py tests/test_risk.py
git commit -m "feat: root provider arm gate + critical-risk privileged shell"
```

---

### Task 3: `build_runtime` wiring (opt-in, Shizuku-preferred) + CLI

**Files:**
- Modify: `src/phonectl/cli.py`, `src/phonectl/config.py` (`enable_root_provider` default)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- `config.DEFAULTS["enable_root_provider"] = False`.
- `cli._make_root_provider(cfg)` returns a `RootProvider(enabled=True)` **only** when
  `cfg["enable_root_provider"]` and `is_available()`; else `None`. `build_runtime` registers it **after**
  Shizuku (so when both privileged providers exist, the safer Shizuku wins for a given capability), and only
  under the explicit flag.
- CLI: `phonectl root status`, `phonectl root arm`/`disarm`, `phonectl root shell <cmd>` — `shell` routes
  through `run_action` (critical ⇒ confirm/one-time approval).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py — append
def test_root_not_registered_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    reg = cli.build_runtime(config.load())
    assert reg.capabilities().get("privileged_shell", False) is False


def test_shizuku_preferred_over_root_for_privileged(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    from phonectl.providers.shizuku import ShizukuProvider
    from phonectl.providers.root import RootProvider

    class _B:
        def __call__(self, argv):
            class R: pass
            r = R(); r.stdout = "ok"; r.stderr = ""; r.returncode = 0
            return r

    monkeypatch.setattr(cli, "_make_shizuku_provider",
                        lambda cfg: ShizukuProvider(bridge=_B(), which=lambda n: "/x/rish"))
    monkeypatch.setattr(cli, "_make_root_provider",
                        lambda cfg: RootProvider(su_runner=_B(), which=lambda n: "/sbin/su", enabled=True))
    reg = cli.build_runtime({**config.load(), "enable_shizuku": True, "enable_root_provider": True})
    # both present; the privileged provider that resolves first should be Shizuku
    reg._require("privileged_shell")
    assert reg.last_used == "ShizukuProvider"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v -k "root or preferred"`
Expected: FAIL (`AttributeError: _make_root_provider`).

- [ ] **Step 3: Write minimal implementation**

Add `enable_root_provider=False` to `config.DEFAULTS`; add `cli._make_root_provider(cfg)`; in
`build_runtime` insert the Shizuku provider before the root provider in the privileged slot (both ahead of
ADB, Shizuku first), each gated by its flag. Add the `root` CLI subparser whose `shell` handler routes
through `run_action`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v -k "root or preferred"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py src/phonectl/config.py tests/test_cli.py
git commit -m "feat: opt-in root provider registration (Shizuku-preferred) + root CLI verbs via run_action"
```

---

### Task 4: Docs + roadmap note (loud disclosure)

**Files:**
- Modify: `README.md`, `docs/superpowers/phonectl-platform-roadmap.md`
- Create: `docs/root.md`
- Test: *(docs-only — run the full suite)*

**Interfaces (docs content):** `docs/root.md` states plainly that root is the most dangerous provider, is
**off by default**, requires **two** explicit steps (`enable_root_provider=true` **and** a per-session
`root arm`), runs every op at **critical** risk, and is never required for core value; documents the
`su -c` contract, the arm/disarm lifecycle, and how to fully disable. README links it under "Advanced
providers" with the same warning framing.

- [ ] **Step 1:** Write `docs/root.md` + the README note + the roadmap mark.
- [ ] **Step 2:** No code changes; run `pytest -v` to confirm green.
- [ ] **Step 3: Commit**

```bash
git add README.md docs/root.md docs/superpowers/phonectl-platform-roadmap.md
git commit -m "docs: root provider — loud, default-off disclosure + arm lifecycle"
```

---

## Dependencies

- **Plan 1.1** (`capabilities`/`results`/`errors`), **Plan 3.1** (`ProviderRegistry`/`build_runtime`),
  **Plan 2.2** (`risk` critical set), **Plan 7.1** (shares the privileged capability keys + the
  Shizuku-preferred ordering). Opportunistic on **Plan 2.3** (MCP) — intentionally **not** auto-exposed as
  an MCP tool by default (root over an agent tool is opt-in beyond the flag; documented).

## Deferred / out of scope

- **MCP root tools** — deliberately omitted by default; exposing root to an agent is a separate, opt-in
  decision beyond this plan.
- **Per-command root allowlists** — beyond the critical-risk gate + arm, finer policy is a follow-up.

## Notes on testability

- The `su_runner` seam is fully injectable — every command is asserted against a **fake**; no real `su`,
  device, or `subprocess` runs in CI.
- Default-deny, the arm gate, the Shizuku-preferred ordering, and critical-risk classification are all
  unit-tested with fakes.
- **No device behavior is claimed.** A real rooted-device run is a manual on-device smoke (note it in
  `docs/root.md`; never run it in CI).
