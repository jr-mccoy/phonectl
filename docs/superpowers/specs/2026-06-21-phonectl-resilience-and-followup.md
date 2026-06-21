# phonectl — Follow-up work: resilience, polish & deferred backlog

**Date:** 2026-06-21
**Status:** Backlog / next-iteration planning
**Author:** Jeremy McCoy (with Claude)
**Predecessors:** [`2026-06-20-phonectl-adb-bridge-design.md`](2026-06-20-phonectl-adb-bridge-design.md) (spec), [`../plans/2026-06-20-phonectl-observe-act-core.md`](../plans/2026-06-20-phonectl-observe-act-core.md) (plan)

## 1. Where we are

The **observe→act→observe core** is built, unit-tested (**45 tests, stdlib-only runtime**), reviewed
(final whole-branch review verdict: merge-ready after fixes), and **validated on a real device**
(Samsung Galaxy S25 Ultra, Android, Wireless Debugging over loopback from inside Termux + PRoot).

**Proven working on-device:**
- Build-step-zero connectivity: `adb pair` → `adb connect` → `adb shell echo ok` from inside the PRoot
  distro (no host-Termux shim needed — the shared-loopback topology holds).
- `observe()` parses real `uiautomator` XML into indexed elements with correct screen dims and a stable hash.
- `act()` loop: `launch`, `key`, `tap` each act and re-observe; the screen-hash changes between
  pre/post snapshots, confirming actions land.
- `connection.ensure()` correctly detects a dropped link and surfaces the re-enable guidance instead of crashing.
- Safety gating (`auto`/`confirm`/`dry-run` + `STOP` kill-switch + JSONL audit log) verified via tests.

**Already fixed in the post-review fix pass** (commit `849245a`):
- `input_text` now `shlex.quote`s text (shell metacharacters no longer interpreted by the device shell).
- `window_dump` uses `dumpsys window` (not `dumpsys window windows`) — the latter omits `mCurrentFocus`
  on this device, which left `observe().app` blank. **Found by the real-device smoke test.**
- `type` no longer writes raw text to the audit log / confirm / dry-run output — it logs a redacted
  `<N chars>` surrogate while the actuator still receives the real text (no more cleartext passwords/OTPs).
- `from __future__ import annotations` added to `observer.py`/`cli.py` so the `requires-python = ">=3.9"`
  claim is honest (PEP 604 `X | None` annotations were evaluating eagerly).
- README corrected: `launch` uses the `monkey` launcher intent, not `am start`.

## 2. Known issues observed in real use (highest priority)

These two are the difference between "works in a demo" and "works unattended." Both are manifestations of
items the original plan **deliberately deferred**, but real-device testing showed they bite quickly.

### 2.1 Wireless-Debugging connect-port volatility → silent disconnect
**Symptom:** After the phone slept, the wireless-debugging **connect port rotated** and the established
connection was torn down by the ROM. `adb connect <ip>:<old-port>` then returns `Connection refused`, and
every `phonectl` command fails with the `ConnectionError` guidance until the user reads the *new* port off
the phone and reconnects by hand.

**Root cause / constraint:** Android regenerates the wireless-debugging connect port across
disconnect/sleep cycles. The spec's intended mitigation — `adb mdns services` auto-rediscovery — **does not
work inside this PRoot environment** (no mDNS daemon; `adb mdns services` returns an empty list).

**Proposed work:**
- Implement `connection` auto-rediscovery with a layered strategy:
  1. Try `adb mdns services` (works on environments that have an mDNS responder).
  2. Fallback for PRoot/Termux: a bounded **port probe** of the device IP across the wireless-debugging
     port range, or read the port from a known source if one exists.
  3. Final fallback: the **host-Termux shim** path (run `adb` from host Termux, which may have mDNS),
     keeping the `adb_backend` interface unchanged (already anticipated in the spec §4.1).
- Persist the last-known-good port and retry it first.
- Consider a `phonectl reconnect <port>` convenience verb so manual recovery is one short command.

### 2.2 `observe()` crashes on `uiautomator` "could not get idle state"
**Symptom:** When the screen is asleep, mid-animation, or on the lock screen, `uiautomator dump` returns the
literal text `ERROR: could not get idle state.` (not XML). `ui_parser.parse_elements` then raises an
unhandled `xml.etree.ElementTree.ParseError` and `phonectl observe` dies with a traceback.

**Root cause:** The plan deferred "`uiautomator` retry/settle on animated screens," and the backend treats
any `ui_dump()` output as XML.

**Proposed work:**
- In `adb_backend.ui_dump()` / `observer.observe()`: detect a non-XML / `ERROR:`-prefixed dump and either
  (a) **retry with a short settle delay** (a few attempts), then (b) raise a **clear, typed error**
  (e.g. `ObserveError("screen not idle — is it asleep or locked?")`) instead of a raw `ParseError`.
- Add `ensure()` **auto-`WAKEUP`** (spec §8 explicitly lists "Device asleep → `input keyevent WAKEUP`",
  but the current `ensure()` only does get-state + reconnect). Waking before observe would have prevented
  most of the failures seen in testing.
- Detect the **lock screen** (`dumpsys window` keyguard state) and report it clearly — we cannot pass a PIN
  without root (spec non-goal), so a precise "device is locked, unlock it" message is the correct behavior.

## 3. Deferred backlog (from the original plan — unchanged scope)

These were always planned as follow-on work; listed here so they live in one place:

- **mDNS auto-discovery** for silent reconnect after reboot (see §2.1 for the PRoot caveat).
- **Full `phonectl setup` interactive onboarding wizard** (install adb, guide Wireless Debugging, pair,
  persist config + adbkey).
- **Guarded-package denylist enforcement** in the action path (spec §9) — refuse/force-confirm on
  banking/purchase screens keyed off `observe().app.package`.
- **Rate limiting** (spec §9) — cap actions/min to bound runaway loops. *Silently absent today; should be on
  the backlog explicitly.*
- **MCP server wrapper** exposing the verbs as native agent tools (spec §5, phase 2).
- **AccessibilityService APK backend** behind the same `adb_backend`-shaped interface (spec §4.2, phase 6).
- **Density-aware swipe scaling** and per-device coordinate math (spec §13).

## 4. Polish / minor findings (from reviews — non-blocking)

Carried from the per-task reviews and the final whole-branch review:

- **`swipe` direction form not implemented.** Spec §5.2 lists `swipe(dir | x1,y1→x2,y2)`; v1 ships
  coordinate-only. Either implement named directions (`up/down/left/right`) or move it to the deferred list
  in the spec so the contract isn't overstated.
- **`adb_backend._adb_bytes` test-only sentinel.** `res._bytes if hasattr(res, "_bytes") else res.stdout`
  lets production probe a test attribute. Drop the sentinel; have the test double expose bytes via `.stdout`
  and simplify `_adb_bytes` to `return res.stdout`, so the real bytes path is exercised.
- **`cli` confirm/dry-run messages** print the raw target dict repr — `json.dumps(target)` reads cleaner.
- **`cli._guard_action(cfg)`** takes an unused `cfg` param — drop it (or pass it to `kill_switch_active`).
- **`dry-run` discards the observed snapshot** — emitting it would make dry-run a richer preview.
- **`observer` orientation** is derived purely from `w` vs `h`; could read the hierarchy root `rotation`
  attribute and fall back to the heuristic.
- **`ui_parser.screen_hash`** encodes `bounds` via Python list-repr; `','.join(map(str, bounds))` is robust
  against future container-type drift (not a current bug — no hash is persisted).
- **`actuator.wait_for`** uses an iteration-count decrement rather than wall-clock; only pathological if a
  caller passes `interval=0` in production (default 0.5 is fine). Could switch to a `monotonic()` deadline
  if `interval=0` ever needs to be supported.
- **`actuator.wait_for(..., id=...)`** parameter shadows the builtin `id` — kept intentionally (it's the
  public kwarg the CLI passes); rename only if a linter policy demands it.
- **Spec §5.1 example** omits `content_desc`, which the shipped element shape includes — update the doc
  example to match.

## 5. Suggested sequencing for the next iteration

1. **Resilience first (§2):** `ensure()` auto-`WAKEUP` + observe retry/settle + clear non-XML/lock errors,
   then the layered reconnect/port-recovery strategy. This is what makes the tool usable unattended.
2. **Safety completeness (§3):** rate limiting + guarded-package enforcement (the spec §9 model is only
   half-shipped without them).
3. **Onboarding (§3):** the `phonectl setup` wizard, so other users can reach build-step-zero without the
   manual pairing dance.
4. **MCP wrapper (§3):** expose verbs as native agent tools — the original phase-2 milestone.
5. **Polish (§4)** folded in opportunistically as the above files are touched.
