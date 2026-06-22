# phonectl Shizuku Privileged Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan 7.1 of the platform roadmap** (`docs/superpowers/phonectl-platform-roadmap.md`). First plan of
Phase 7 (ecosystem & advanced providers). Adds an **opt-in, runtime-discovered Shizuku provider** that
unlocks privileged Android operations (privileged shell, `pm` package ops, secure-settings writes,
low-level input injection) **without rooting the device** (strategy §13.2 Shizuku). Depends on **Plan 1.1**
(`capabilities`/`results`/`errors`/`backend.Backend`), **Plan 3.1** (`ProviderRegistry` +
`cli.build_runtime`), and **Plan 2.2** (`risk` — privileged ops are high/critical-risk and gated by the
existing policy). It is **never a hard dependency**: discovered at runtime, opt-in via config, and the
platform degrades to ADB/Termux providers when Shizuku is absent (strategy §13, §19).

**Goal:** give a user who has set up Shizuku a strictly-better, **clearly-labeled, opt-in** provider for
operations ADB-no-root cannot do cleanly (grant/revoke runtime permissions, write secure settings, force
input on locked-down apps), while keeping phonectl's invariants: capability discovery, structured results,
backend isolation, and one policy choke-point. This plan ships the **Python-side provider seam + an Android
design spec** for the Shizuku binder client; the native client (a tiny Shizuku `UserService` or the
`rish`/`shizuku` CLI bridge) is built from the spec in a separate native effort.

**Architecture:** a new `src/phonectl/providers/shizuku.py` (`ShizukuProvider`) that talks to Shizuku
**only** through an injectable `bridge` seam — a callable that runs a command in the Shizuku-elevated
context (default: the `rish`/`shizuku` shell bridge discovered on PATH; tests inject a fake). It implements
`backend.Backend` privileged methods and reports its capabilities **only when Shizuku is reachable and
phonectl is authorized**. `cli.build_runtime` registers it **first** (highest priority for privileged
capabilities) **only when `enable_shizuku` is true in config**, so default behavior is byte-for-byte
unchanged. Privileged verbs route through `runtime.run_action` (single writer + risk policy), never a
bypass.

**Tech Stack:** Python 3 (stdlib only: `subprocess`, `shutil`, `json`); `pytest`. No new runtime dep. The
Android side (Shizuku `UserService`/CLI bridge) is **design-spec only** here.

## Global Constraints

- **stdlib-only at runtime.** `subprocess`, `shutil`, `json` — all stdlib.
- **Backend isolation.** Only `shizuku.py` knows about the Shizuku bridge; it talks to it through the
  injected `bridge` runner, never letting other modules call it. Like `adb_backend`, it is the **only**
  place that touches its transport.
- **Opt-in + runtime-discovered.** `ShizukuProvider.is_available()` probes the bridge; `capabilities()`
  returns all-false unless available **and** authorized. `build_runtime` registers it only when
  `enable_shizuku` is true. With Shizuku absent/disabled, every existing test and behavior is unchanged
  (strategy §13, §19).
- **One policy choke-point.** Privileged verbs go through `runtime.run_action`; they are classified
  **high/critical** by `risk` (2.2) so they require confirmation/grant by default. The provider adds no
  bypass.
- **Structured-result invariant.** Provider methods return data the runtime wraps in `results.ok`/`err`;
  an unauthorized/absent bridge raises `errors.CapabilityUnavailableError` with an actionable
  `user_action` (how to install/authorize Shizuku).
- **Injectable seams.** `ShizukuProvider(*, bridge=<runner>, which=shutil.which)`; tests inject a fake
  bridge that records commands. `PHONECTL_HOME` isolation for config.
- **Clearly labeled, separated.** Shizuku is an *advanced* provider; setup/docs spell out the trust
  implications. It is not required for any core value.
- **One commit per task. TDD order is non-negotiable.**

## Shared conventions established by this plan

- **New capability keys** (added to `capabilities.CAPABILITY_KEYS`): `privileged_shell`, `privileged_pm`,
  `privileged_settings`, `privileged_input`. (`write_secure_settings` already exists and is also reported.)
- **Bridge contract:** `bridge(argv: list[str]) -> CompletedProcess`-like (`stdout`, `stderr`,
  `returncode`). Default bridge runs `rish -c <cmd>` / `shizuku` shell when discovered on PATH; the
  provider never assumes a specific binary beyond what `which` finds.
- **Config key added:** `enable_shizuku` (bool, default `false`). Non-true ⇒ the provider is never
  registered.
- **Risk:** the new privileged verbs are added to the Plan-2.2 `HIGH_RISK_VERBS`/`CRITICAL_VERBS` sets
  (e.g. `pm_uninstall`/`settings_put_global` → critical) so the policy gate treats them conservatively.

---

### Task 1: Capability keys + `ShizukuProvider` discovery + `capabilities()`

**Files:**
- Modify: `src/phonectl/capabilities.py`
- Create: `src/phonectl/providers/shizuku.py`
- Test: `tests/test_shizuku_provider.py`

**Interfaces:**
- New keys in `CAPABILITY_KEYS`: `privileged_shell`, `privileged_pm`, `privileged_settings`,
  `privileged_input`.
- `ShizukuProvider(*, bridge=None, which=shutil.which)`. `is_available() -> bool` — the bridge binary is on
  PATH **and** a probe command (`echo ok` through the bridge) succeeds with returncode 0.
- `capabilities() -> dict` — all-false when unavailable; otherwise `privileged_shell`/`privileged_pm`/
  `privileged_settings`/`privileged_input`/`write_secure_settings` = True.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shizuku_provider.py
from phonectl import capabilities as caps
from phonectl.providers.shizuku import ShizukuProvider


class FakeBridge:
    def __init__(self, ok=True, out="ok\n"):
        self.ok = ok
        self.out = out
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        class R:
            pass
        r = R()
        r.stdout = self.out
        r.stderr = ""
        r.returncode = 0 if self.ok else 1
        return r


def test_new_capability_keys_exist():
    c = caps.make(privileged_shell=True, privileged_pm=True,
                  privileged_settings=True, privileged_input=True)
    assert c["privileged_shell"] and c["privileged_input"]


def test_unavailable_when_no_bridge_binary():
    p = ShizukuProvider(bridge=FakeBridge(), which=lambda name: None)
    assert p.is_available() is False
    assert p.capabilities()["privileged_shell"] is False


def test_available_reports_privileged_caps():
    p = ShizukuProvider(bridge=FakeBridge(ok=True), which=lambda name: "/usr/bin/rish")
    assert p.is_available() is True
    c = p.capabilities()
    assert c["privileged_shell"] and c["privileged_pm"] and c["privileged_input"]
    assert c["write_secure_settings"] is True


def test_probe_failure_is_unavailable():
    p = ShizukuProvider(bridge=FakeBridge(ok=False), which=lambda name: "/usr/bin/rish")
    assert p.is_available() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_shizuku_provider.py -v`
Expected: FAIL (`ValueError: unknown capability keys` / `ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/capabilities.py — append to CAPABILITY_KEYS
    # Phase 7.1/7.2 additions (privileged providers — Shizuku / root)
    "privileged_shell",
    "privileged_pm",
    "privileged_settings",
    "privileged_input",
```

```python
# src/phonectl/providers/shizuku.py
"""Opt-in Shizuku privileged provider — runtime-discovered, never a hard dependency."""
from __future__ import annotations

import shutil
import subprocess

from phonectl import capabilities as caps_mod

_BRIDGE_BINS = ("rish", "shizuku")


def _default_bridge(argv):
    for binname in _BRIDGE_BINS:
        if shutil.which(binname):
            return subprocess.run([binname, "-c", " ".join(argv)],
                                  capture_output=True, text=True)
    raise FileNotFoundError("no Shizuku bridge binary on PATH")


class ShizukuProvider:
    def __init__(self, *, bridge=None, which=shutil.which) -> None:
        self._bridge = bridge or _default_bridge
        self._which = which

    def _bridge_present(self) -> bool:
        return any(self._which(b) for b in _BRIDGE_BINS)

    def is_available(self) -> bool:
        if not self._bridge_present():
            return False
        try:
            res = self._bridge(["echo", "ok"])
            return getattr(res, "returncode", 1) == 0
        except Exception:
            return False

    def capabilities(self) -> dict:
        if not self.is_available():
            return caps_mod.make()
        return caps_mod.make(
            privileged_shell=True,
            privileged_pm=True,
            privileged_settings=True,
            privileged_input=True,
            write_secure_settings=True,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_shizuku_provider.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/capabilities.py src/phonectl/providers/shizuku.py tests/test_shizuku_provider.py
git commit -m "feat: Shizuku provider discovery + privileged capability keys"
```

---

### Task 2: Privileged shell + secure-settings writes

**Files:**
- Modify: `src/phonectl/providers/shizuku.py`
- Test: `tests/test_shizuku_provider.py` (append)

**Interfaces:**
- `ShizukuProvider.privileged_shell(cmd: str) -> str` — run a command in the elevated context; raise
  `errors.CapabilityUnavailableError` if unavailable, `errors.PhonectlError("shizuku_failed")` on non-zero
  return.
- `ShizukuProvider.settings_put(namespace, key, value) -> None` — `settings put <namespace> <key> <value>`
  (namespace ∈ `system|secure|global`) via the bridge.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shizuku_provider.py — append
import pytest
from phonectl import errors


def _avail():
    return ShizukuProvider(bridge=FakeBridge(ok=True), which=lambda n: "/usr/bin/rish")


def test_privileged_shell_runs_via_bridge():
    p = _avail()
    out = p.privileged_shell("id")
    assert "ok" in out
    assert p._bridge.calls[-1] == ["id"]


def test_privileged_shell_unavailable_raises():
    p = ShizukuProvider(bridge=FakeBridge(), which=lambda n: None)
    with pytest.raises(errors.CapabilityUnavailableError):
        p.privileged_shell("id")


def test_settings_put_builds_command():
    p = _avail()
    p.settings_put("secure", "k", "v")
    assert p._bridge.calls[-1] == ["settings", "put", "secure", "k", "v"]


def test_settings_put_rejects_bad_namespace():
    p = _avail()
    with pytest.raises(ValueError):
        p.settings_put("bogus", "k", "v")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_shizuku_provider.py -v -k "shell or settings"`
Expected: FAIL (`AttributeError: privileged_shell`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/shizuku.py — append
from phonectl import errors

_NAMESPACES = {"system", "secure", "global"}


class ShizukuProvider:  # methods added to the class above
    def _run(self, argv):
        if not self.is_available():
            raise errors.CapabilityUnavailableError(
                "Shizuku is not available; install + start Shizuku and grant phonectl access "
                "(see docs/shizuku.md).")
        res = self._bridge(argv)
        if getattr(res, "returncode", 1) != 0:
            raise errors.PhonectlError(
                f"shizuku command failed: {' '.join(argv)}: {getattr(res, 'stderr', '')}")
        return getattr(res, "stdout", "")

    def privileged_shell(self, cmd):
        return self._run(cmd.split() if isinstance(cmd, str) else list(cmd))

    def settings_put(self, namespace, key, value):
        if namespace not in _NAMESPACES:
            raise ValueError(f"bad settings namespace {namespace!r}")
        self._run(["settings", "put", namespace, key, str(value)])
```

(Give `errors.PhonectlError` a stable `code` by subclassing if a `shizuku_failed` code is wanted; the
generic `PhonectlError` is acceptable since the message is actionable.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_shizuku_provider.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/shizuku.py tests/test_shizuku_provider.py
git commit -m "feat: Shizuku privileged_shell + secure-settings writes (guarded by availability)"
```

---

### Task 3: Package management (`pm grant/revoke/install/uninstall`)

**Files:**
- Modify: `src/phonectl/providers/shizuku.py`, `src/phonectl/risk.py` (mark privileged pm verbs)
- Test: `tests/test_shizuku_provider.py` (append), `tests/test_risk.py` (append)

**Interfaces:**
- `ShizukuProvider.pm_grant(package, permission)` / `pm_revoke(...)` / `pm_install(apk_path)` /
  `pm_uninstall(package)` — each via `pm …` through the bridge.
- `risk`: add `pm_uninstall`/`settings_put_global` to `CRITICAL_VERBS`; `pm_grant`/`pm_revoke`/`pm_install`
  to `HIGH_RISK_VERBS` (Plan 2.2 sets) so the policy gate treats them conservatively.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shizuku_provider.py — append
def test_pm_grant_and_uninstall_commands():
    p = _avail()
    p.pm_grant("com.x", "android.permission.CAMERA")
    assert p._bridge.calls[-1] == ["pm", "grant", "com.x", "android.permission.CAMERA"]
    p.pm_uninstall("com.x")
    assert p._bridge.calls[-1] == ["pm", "uninstall", "com.x"]
```

```python
# tests/test_risk.py — append
def test_privileged_pm_verbs_are_high_or_critical():
    from phonectl import risk
    assert "pm_uninstall" in risk.CRITICAL_VERBS
    assert "pm_grant" in risk.HIGH_RISK_VERBS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_shizuku_provider.py tests/test_risk.py -v -k "pm"`
Expected: FAIL (`AttributeError: pm_grant` / verb not in set).

- [ ] **Step 3: Write minimal implementation**

```python
# src/phonectl/providers/shizuku.py — append
    def pm_grant(self, package, permission):
        self._run(["pm", "grant", package, permission])

    def pm_revoke(self, package, permission):
        self._run(["pm", "revoke", package, permission])

    def pm_install(self, apk_path):
        self._run(["pm", "install", apk_path])

    def pm_uninstall(self, package):
        self._run(["pm", "uninstall", package])
```

```python
# src/phonectl/risk.py — extend the Plan-2.2 sets
CRITICAL_VERBS = CRITICAL_VERBS | {"pm_uninstall", "settings_put_global"}
HIGH_RISK_VERBS = HIGH_RISK_VERBS | {"pm_grant", "pm_revoke", "pm_install", "privileged_shell"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_shizuku_provider.py tests/test_risk.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/providers/shizuku.py src/phonectl/risk.py tests/test_shizuku_provider.py tests/test_risk.py
git commit -m "feat: Shizuku pm grant/revoke/install/uninstall + conservative risk classification"
```

---

### Task 4: `build_runtime` wiring (opt-in, highest priority for privileged caps) + CLI

**Files:**
- Modify: `src/phonectl/cli.py`, `src/phonectl/config.py` (`enable_shizuku` default)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- `config.DEFAULTS["enable_shizuku"] = False`.
- `cli._make_shizuku_provider(cfg)` returns a `ShizukuProvider` **only** when `cfg["enable_shizuku"]` and
  `is_available()`; else `None`. `build_runtime` prepends it (so it wins for privileged capabilities) when
  present, leaving ADB/Termux/companion order otherwise unchanged.
- CLI: `phonectl shizuku status` (reports availability + capabilities), `phonectl shizuku shell <cmd>`,
  `phonectl settings put <ns> <k> <v>`, `phonectl pm grant|revoke|install|uninstall …` — privileged verbs
  route through `runtime.run_action` (so confirm/policy apply).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py — append
def test_shizuku_not_registered_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    reg = cli.build_runtime(config.load())
    # default config has enable_shizuku=False → no privileged caps
    caps = reg.capabilities() if hasattr(reg, "capabilities") else reg[0].capabilities()
    assert caps.get("privileged_shell", False) is False


def test_shizuku_registered_when_enabled_and_available(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    from phonectl.providers.shizuku import ShizukuProvider

    class _FakeBridge:
        def __call__(self, argv):
            class R: pass
            r = R(); r.stdout = "ok"; r.stderr = ""; r.returncode = 0
            return r

    monkeypatch.setattr(cli, "_make_shizuku_provider",
                        lambda cfg: ShizukuProvider(bridge=_FakeBridge(), which=lambda n: "/x/rish"))
    monkeypatch.setattr(cli, "_make_backend", lambda cfg: FakeBackend())
    reg = cli.build_runtime({**config.load(), "enable_shizuku": True})
    assert reg.capabilities()["privileged_shell"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v -k "shizuku"`
Expected: FAIL (`AttributeError: _make_shizuku_provider`).

- [ ] **Step 3: Write minimal implementation**

Add `enable_shizuku=False` to `config.DEFAULTS`; add `cli._make_shizuku_provider(cfg)` and prepend it in
`build_runtime` when non-`None`; add the `shizuku`/`settings`/`pm` CLI subparsers whose privileged handlers
call `_do_action`/`run_action` so the policy gate applies. Keep the default ordering identical when the
provider is absent.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v -k "shizuku"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/cli.py src/phonectl/config.py tests/test_cli.py
git commit -m "feat: opt-in Shizuku registration in build_runtime + privileged CLI verbs via run_action"
```

---

### Task 5: Android/setup design spec for the Shizuku bridge

**Files:**
- Create: `docs/superpowers/specs/2026-06-22-phonectl-shizuku-bridge-design.md`
- Test: *(docs-only task — no code; verify the suite is still green)*

**Interfaces (spec content):** the Shizuku authorization model (Shizuku app + ADB/wireless start), the two
bridge options (the `rish`/`shizuku` shell CLI vs a tiny `UserService` binder client), the command
contract the Python `bridge` seam expects, the trust/permission UX (how a user authorizes phonectl, how to
revoke), the capability mapping (which privileged ops Shizuku unlocks vs ADB-no-root), and the safety
disclosures (privileged ops are high/critical risk). Mirrors the design-spec discipline of the Phase-4
plans' Android spec task.

- [ ] **Step 1:** Write the spec (goal, non-goals, authorization model, bridge contract, capability
  mapping, trust UX, security model, risks, handoff to the Python seam shipped in Tasks 1–4).
- [ ] **Step 2:** No code changes; run `pytest -v` to confirm nothing regressed.
- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-06-22-phonectl-shizuku-bridge-design.md
git commit -m "docs: Shizuku bridge Android/authorization design spec"
```

---

### Task 6: MCP tools + docs

**Files:**
- Modify: `src/phonectl/mcp_server.py`, `README.md`, `docs/superpowers/phonectl-platform-roadmap.md`
- Create: `docs/shizuku.md`
- Test: `tests/test_mcp_server.py` (append)

**Interfaces:**
- MCP: `phone.shizuku.status`, `phone.shizuku.shell`, `phone.settings.put`, `phone.packages.grant`/
  `revoke` added to the Plan-2.3 `TOOLS` registry, each returning the `results` envelope and gated by
  capability discovery.
- `docs/shizuku.md`: setup walkthrough, trust implications, capability list, the `enable_shizuku` opt-in,
  and the "advanced, opt-in, not required for core value" framing.
- Run the **full suite** on this final task.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_server.py — append
def test_shizuku_tools_registered():
    from phonectl import mcp_server
    assert "phone.shizuku.shell" in mcp_server.TOOLS
    assert "phone.settings.put" in mcp_server.TOOLS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_server.py -v -k "shizuku"`
Expected: FAIL (tool not registered).

- [ ] **Step 3:** Register the `phone.shizuku.*`/`phone.settings.put`/`phone.packages.grant`/`revoke`
  handlers (each returns the envelope, capability-gated). Write `docs/shizuku.md`; add a README "Advanced
  providers" note; mark 7.1 in the roadmap.

- [ ] **Step 4: Run the full suite, then commit**

Run: `pytest -v`
Expected: PASS (Shizuku is opt-in + discovered; default builds are unchanged).

```bash
git add src/phonectl/mcp_server.py README.md docs/shizuku.md docs/superpowers/phonectl-platform-roadmap.md tests/test_mcp_server.py
git commit -m "feat: Shizuku MCP tools + docs"
```

---

## Dependencies

- **Plan 1.1** (`capabilities`/`results`/`errors`), **Plan 3.1** (`ProviderRegistry`/`build_runtime`),
  **Plan 2.2** (`risk` sets the privileged verbs join). Opportunistic on **Plan 5.1** (`_dispatch` routing)
  and **Plan 2.3** (MCP registry).

## Deferred / out of scope

- **The native Shizuku `UserService` binder client** (Kotlin) — built from the Task-5 spec in a separate
  native effort; this plan ships only the Python seam + the CLI/MCP surface + the shell-bridge default.
- **Per-permission allowlists for privileged ops** — beyond the risk gate, a fine-grained
  Shizuku-permission policy is a follow-up.

## Notes on testability

- The `bridge` seam is fully injectable — every privileged command is asserted against a **fake bridge**;
  no real Shizuku, device, or `subprocess` runs in CI.
- Capability discovery, opt-in registration, and risk classification are unit-tested with fakes.
- **No device behavior is claimed.** A real Shizuku-authorized run is a manual on-device smoke (note it in
  `docs/shizuku.md`; do not run it in CI).
