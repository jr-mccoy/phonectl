# phonectl — Android computer-use bridge over ADB (no root)

**Date:** 2026-06-20
**Status:** Design approved, pending spec review
**Author:** Jeremy McCoy (with Claude)

## 1. Goal

Give an AI agent running inside a Termux + PRoot-Distro environment the ability to
**observe and control the host Android phone like a human** — the "computer use" loop,
on the device itself. The agent reads the screen as structured data, acts on it
(tap/type/swipe/launch), and re-observes, driving arbitrary apps.

**Primary milestone:** the full `observe → act → observe` loop over any app.
`observe()` is built first as the foundation; acting blind is useless.

## 2. Non-goals

- **No root.** The product targets unrooted phones; this is the core value for release.
  Root-only capabilities (private-app data, `FLAG_SECURE` screenshots, passing a lock-screen
  PIN) are explicitly out of scope. At most a future optional "if rooted, also…" layer.
- Not a cross-device cloud service. Everything runs locally on the phone.
- Not a vision-first agent. Screenshots are a fallback; the structured UI tree is primary.

## 3. Constraints & environment (verified 2026-06-20)

- Runtime: agent lives in a **PRoot-Distro** (`proot@termux`) inside **Termux** inside
  **Android (unrooted)**. `uid 0` in the distro is proot-root, not device root.
- No `adb`, `termux-api`, or `nc` present in the distro yet; host Termux is real
  (`/data/data/com.termux`, `termux-setup-storage` available).
- PRoot does **not** isolate the network namespace, so `127.0.0.1` in the distro is the
  device loopback.
- Requires **Android 11+** Wireless Debugging for the no-root ADB path.

## 4. Architecture

### 4.1 Topology

```
Android (unrooted)
  adbd  ── Wireless Debugging, listening on 127.0.0.1:<port>
    ▲  loopback TCP (shared net namespace)
  Termux
    PRoot distro (agent lives here)
      agent → phonectl → adb client → 127.0.0.1:<port>
```

`adb` runs **inside the PRoot distro**, co-located with the agent. PRoot shares Termux's
(and Android's) loopback, so the adb client dials `adbd` directly — no bridge daemon, no
namespace hopping. **Fallback:** if PRoot ever blocks the connection, call host Termux's
`adb` through a thin shim; the interface above is unchanged.

### 4.2 Backend abstraction

All adb knowledge is confined to one module (`adb_backend`). Everything above it speaks a
backend-agnostic interface, so a future **AccessibilityService APK backend** is a drop-in
swap with no changes to `observer`, `actuator`, or the CLI.

**Provider graph (Phase 3.1):** The `Backend` seam is now a `ProviderRegistry`
(`src/phonectl/providers/registry.py`) that selects the best-available provider per
capability. `AdbBackend` is the sole provider in Phase 3.1, but the registry is
extensible: adding `TermuxApiProvider` (Phase 3.5) or `AccessibilityServiceProvider`
(Phase 4.1) means prepending it to the list in `cli.build_runtime()` with no other
changes required. Every result envelope includes a `provider` field (the class name of
the provider that handled the last call) so callers can observe which path was used.

**AccessibilityService is an additional `Backend` provider (Phase 4.1):** `AccessibilityProvider`
(`src/phonectl/providers/accessibility.py`) talks to a companion Android AccessibilityService APK
through an injected `Transport` (`src/phonectl/providers/transport.py`). It never calls
`adb`/`subprocess` and never imports `adb_backend`. It is prepended to the provider list in
`build_runtime()` ahead of `TermuxApiProvider` and `AdbBackend`, so it wins for
`observe_ui_tree`/`act_*` when the companion is reachable. ADB remains the shell/system provider
— `wake`, `keyguard`, `get_state`, and other ADB-specific helpers fall through to `AdbBackend`
via `ProviderRegistry.__getattr__`, so the companion never has to reimplement shell access. When
the companion is absent, `_make_accessibility_provider()` returns `None` and the registry is
ADB-first, unchanged.

**Power rationale (why ADB, not the a11y APK, is the first backend):** ADB runs as the
`shell` user (uid 2000) and is system-wide — a superset of the AccessibilityService's
abilities. It can drive the UI (`uiautomator` + `input`) **and** reach beneath it
(`am`/`pm`/`settings`/`dumpsys`), e.g. `am start` an app instead of navigating to its icon.
The a11y service's only advantages are persistence and event latency, not power.

## 5. The agent-facing contract

Module `phonectl`, built as **library + CLI first** (scriptable, testable), wrapped as an
**MCP server** in phase 2 so verbs appear as native agent tools.

### 5.1 `observe()` → JSON snapshot

```json
{
  "app": {"package": "com.android.settings", "activity": ".Settings"},
  "screen": {"w": 1080, "h": 2400, "orientation": "portrait"},
  "hash": "a1b2c3…",
  "elements": [
    {"i": 7, "text": "Wi-Fi", "id": "android:id/title", "class": "TextView",
     "clickable": true, "bounds": [44, 380, 1036, 520], "center": [540, 450]}
  ],
  "screenshot": "/…/snap-1234.png"
}
```

- `elements` come from `uiautomator dump`, flattened and assigned a **stable index `i`**.
- `app` from `dumpsys window` / `dumpsys activity`.
- `hash` is a digest of the element set; it changes when the screen changes (the basis for
  detecting whether an action landed).
- `screenshot` is optional (vision fallback); on `FLAG_SECURE` screens it may be black, in
  which case the element tree is the source of truth.

### 5.2 `act(...)` verbs

Each verb returns the **post-action `observe()`** so the loop is always observe→act→observe.

- `tap(i | x,y)` — target an element index (preferred) or raw coordinates (escape hatch).
- `type(text)`
- `swipe(dir | x1,y1→x2,y2)`
- `key(back | home | recents | enter | …)`
- `launch(package)` — system-level shortcut unavailable to the a11y path.
- `wait_for(text | id, timeout)` — re-observe until a target appears (or timeout).

**Targeting by element index, not pixels,** lets the same agent logic survive different
screen sizes and minor layout shifts across users' phones.

## 6. Components (isolation boundaries)

| Module | One job | Depends on | Test approach |
|---|---|---|---|
| `adb_backend` | Only module that knows adb: `screencap`, `ui_dump`, `input_*`, `am_start`, `dumpsys`, conn state | adb binary | mock subprocess |
| `ui_parser` | **Pure fn:** uiautomator XML → element list (bounds, center, flags, indices) | none | unit tests on fixtures |
| `observer` | Compose backend calls → snapshot + screen-hash | backend, parser | canned backend |
| `actuator` | Implement `act()` verbs; resolve index→coords via last snapshot; re-observe | backend, session | canned backend |
| `session` | Hold last snapshot, screen dims, conn handle | — | trivial |
| `connection` | Pair, connect, health-check, mDNS rediscover, re-pair, persisted keys | backend | wrong-port sim |
| `cli` (`phonectl`) | Arg parsing → call the above | all | smoke |
| `mcp_server` (phase 2) | Expose verbs as MCP tools | actuator/observer | — |
| `setup` | Interactive onboarding wizard | connection | manual |

## 7. Setup & pairing UX

A `phonectl setup` wizard (the product's first impression for other users):

1. Install `android-tools` (pkg/apt).
2. Guide `Settings → Developer options → Wireless debugging` (detect Android version; require 11+).
3. `adb pair 127.0.0.1:<pairPort>` + 6-digit code (read off that screen, once).
4. **mDNS auto-discovery** (`adb mdns services`) finds the connect port — user never hand-types
   the volatile port.
5. Verify `adb shell echo ok`; persist config (device serial, last ports) and the `adbkey`.

**Friction principle:** pairing is one-time; the port is per-session. Persisted `adbkey` +
mDNS discovery means reconnect after reboot is silent — no codes, no ports.

## 8. Connection recovery

`connection.ensure()` runs before every op:

- `adb get-state` ok → proceed.
- Offline → mDNS rediscover → reconnect (persisted key = no re-auth).
- mDNS finds nothing → Wireless Debugging was disabled by the ROM on reboot → surface the
  **exact** re-enable steps; never fail silently.
- Device asleep → `input keyevent WAKEUP`.
- Locked → detect and report; we cannot pass a PIN without root and will not pretend to.

## 9. Safety guardrails

The agent can tap anything, so v1 ships with:

- **Audit log** — every `act()` appended to JSONL (timestamp, verb, target, resulting app+hash).
- **Three modes** — `auto` (act freely), `confirm` (print intended action, require a confirm
  token before executing), `dry-run` (log only, no injection). **Defaults: `auto` for local
  dev builds, `confirm` for the released build** (set via config, not code).
- **Risk classifier + policy** — every mutating action is classified from the observed
  package, screen text, password fields, OTP-like content, and configured guarded package
  prefixes. Per-level policy maps `low|medium|high|critical` to `allow|confirm|deny`.
- **Kill switch** — a sentinel file that hard-disables all action injection instantly.
- **Bucketed rate limiting** — per-verb, global, and high-risk sliding-window buckets bound
  runaway loops.



### 9.1 Structured result invariant

All new agent-facing JSON surfaces return structured result envelopes rather than
bare tuples or raw tracebacks. The canonical homes are `phonectl.errors` for stable
error codes and retry/user-action flags, `phonectl.results` for `ok`/`err`
envelope builders, and `phonectl.capabilities` for provider capability discovery.
Providers expose capabilities through the backend seam so unavailable features can
be explained before an agent attempts them.

### 9.2 Single-writer action funnel and audit v2

All mutating actions route through `runtime.run_action`. This is the single choke point for
mode checks, the kill switch, request IDs, process-local action serialization, idempotency-key
replay, policy/rate-limit checks, and audit logging. CLI action verbs, the future MCP server,
and the daemon call this funnel instead of reimplementing guardrails at the surface.

Every action envelope and audit record carries a `request_id`. Concurrent mutating callers get
the structured `busy` error instead of racing. A present `STOP` file returns the structured
`stopped` error; confirm mode without `--yes` returns `confirmation_required`.

Audit logging is level-aware via `audit_level`: `none`, `metadata`, `redacted` (default), or
`full`. The default redacted level scrubs OTP-like codes, emails, phone numbers, card-like
numbers, and URL token parameters from audit targets while preserving benign selector labels.

### 9.3 Risk ledger and policy explain

Risk classification now generalizes the earlier guarded-package denylist and single
actions-per-minute limit. `phonectl.risk` and `phonectl.policy` are pure modules: they read the
parsed snapshot and configured policy only, never adb. `runtime.run_action` observes, calls
`policy.explain(snapshot, verb, target, cfg)`, then denies, requires `--yes`, or proceeds. Denied,
confirmation-required, and rate-limited outcomes are returned as structured envelopes with
`risk_level`, `reasons`, and, for rate limits, the blocked `bucket`.

Rate-limit history is persisted as `$PHONECTL_HOME/ratelimit.json`; each allowed action counts
against `global` and its verb bucket, with `high_risk` added for high or critical actions. Audit
records include `outcome` so blocked policy decisions are traceable. The `phonectl policy explain`
verb exposes the same classifier/decision result for agents and users before a mutating action is
attempted.

## 9.1 MCP structured-result frontend

The Phase-2 MCP server is a thin frontend over the existing observation seams and `runtime.run_action`. Observation tools call `observer.observe`/selector matching and return `results.ok` envelopes. Mutating tools never duplicate policy, rate-limit, audit, dry-run, kill-switch, or single-writer logic; they build the same targets as the CLI and route through `run_action`. The MCP transport serializes structured result envelopes directly, preserving `ok`, `error.code`, `requires_user`, `risk_level`, `reasons`, `request_id`, and action data for agent clients. This is the same contract shape the Phase-5 daemon should expose behind JSON-RPC/socket frontends.

## 10. Testing strategy

- **Unit (the bulk):** `ui_parser` against real captured `uiautomator` XML fixtures —
  element extraction, bounds math, centers, clickable flags, index/hash stability. **TDD this
  first**; it is pure and central.
- **Contract:** mock `adb_backend` with canned `screencap`/`dump` → verify `observer` and
  `actuator` compose correctly and index→coord resolution works.
- **Integration smoke (real device):** launch Settings → `wait_for("Wi-Fi")` → tap → assert
  app/hash changed. Manual / device-connected lane.
- **Recovery:** point at a wrong port → assert `ensure()` recovers or gives correct guidance.

## 11. Stack & packaging

- **Python** — already present; strong at XML→JSON, subprocess, and has a first-class MCP SDK
  for phase 2. Chosen over shell because the riskiest logic (XML→structured elements) must be
  a pure, fixture-tested function, not unverifiable string-mangling.
- CLI via argparse; minimal dependencies; `pipx`-installable `phonectl` with a console-script
  entry point. `adb` (android-tools) is the only external dependency.
- Repo ships the `setup` wizard and a short Termux + PRoot install guide.

## 12. Build order (phasing)

0. **Build-step-zero — connectivity proof:** enable Wireless Debugging, `adb pair`, confirm
   `adb shell echo ok` from inside the distro. Validates (or refutes, → host-Termux shim) the
   entire topology before any feature work.
1. `ui_parser` (TDD with fixtures) + `adb_backend.ui_dump`/`screencap`/`dumpsys` → working
   `observe()`.
2. `actuator` verbs + `session` index resolution → full `observe→act→observe` loop (CLI).
3. `connection` recovery + `setup` wizard → reliable, reboot-surviving, installable.
4. Safety guardrails (modes, audit log, risk policy, bucketed rate limits, kill switch).
5. `mcp_server` wrapper → native agent tools.
6. (Optional, later) AccessibilityService APK backend behind the same interface.

## 13. Open risks

- Wireless Debugging port volatility / ROM-specific disable-on-reboot behavior (mitigated by
  mDNS + clear re-enable guidance; cannot be fully eliminated without root).
- `uiautomator dump` occasionally fails or returns stale trees on animated screens → retry +
  settle delay in `observer`.
- adb running inside PRoot may hit ptrace/permission quirks → host-Termux shim fallback.
- Per-device screen-size and density variation → element-index targeting mitigates, but
  swipe distances and some coordinate math need density-awareness.

## Selector and hierarchy observation contract

Snapshot elements keep the stable per-snapshot `i` index and now include richer UI metadata such as `enabled`, `focused`, `checkable`, `checked`, `scrollable`, `long_clickable`, `password`, `selected`, `editable`, `package`, and optional hint/error text when exposed by the device dump. The `screen_hash` remains derived from `text|id|bounds` only.

Selectors are the durable target form across UI reordering, while index `i` remains valid within a single snapshot and raw `(x,y)` remains the escape hatch. A selector is a JSON object with AND semantics over keys such as `text`, `text_regex`, `content_desc`, `resource_id`, `class`, boolean flags, `ancestor_text`, `sibling_text`, `bounds_near`, and zero-based `nth_match`.

`observe(tree=true, relations=true)` may include:

```json
{
  "tree": {"i": null, "class": "android.widget.FrameLayout", "children": []},
  "relations": {
    "parent": {"1": null},
    "children": {"1": []},
    "siblings": {"1": [2]},
    "ancestors": {"1": []}
  }
}
```

Actions can carry `expected_hash` and `stale_ok`. On hash mismatch, the actuator re-observes once and emits/raises the typed `stale_snapshot` failure unless stale execution is explicitly allowed.
