# Companion Startup — Follow-ups & Remaining Work

**Date:** 2026-07-02
**Follows:** PR #45 (`feat: guided phonectl companion setup`) — the companion bring-up
feature. Spec: `docs/superpowers/specs/2026-07-02-phonectl-companion-startup-design.md`;
plan: `docs/superpowers/plans/2026-07-02-phonectl-companion-setup.md`. (Those paths land on
`master` when #45 merges.)

This document is the **backlog / index** of work deferred out of the companion-startup
effort, so nothing is lost. Items 2–4 are real features/remediations that need their **own
brainstorm → spec → plan** before TDD execution (flagged inline); items 1 and 5 are small and
can be executed directly.

---

## 1. On-device `run-as` validation + end-to-end smoke (Task 1 spike) — small, needs a device

The companion-setup plan's **Task 1** (confirm the `run-as` token read on a real debug build)
was deferred because it needs a live device over Wireless Debugging, which kept dropping its
port during development. The code does **not** depend on it — `acquire_token` implements both
the run-as read (B) and the prompt fallback (C) — so this is a confidence/validation task, not
a blocker.

**Do:**
- Reconnect adb, then run `adb -s <serial> shell run-as com.phonectl.companion cat shared_prefs/phonectl_companion.xml`
  and confirm it prints `<string name="companion_token">…</string>`. If denied
  (`run-as: package not debuggable`), record it — the prompt fallback covers that case.
- Run the full flow on a real Samsung Galaxy S25 Ultra:
  `phonectl companion setup --yes --apk <path>` from a clean uninstall, then
  `phonectl companion status`.

**Acceptance:** the run-as outcome is documented; `companion setup --yes` brings the companion
up on-device and `companion status` reports `installed/accessibility/socket/token_paired` all
true; an authenticated `phonectl observe` reads the live tree through the companion.

## 2. ADB Wireless-Debugging port-rotation reconnect — **needs its own brainstorm/spec/plan**

**Problem.** The Wireless-Debugging *connect* port rotates whenever the link drops or the phone
reboots; adb's mDNS discovery is OEM-dependent and was dead on the test device. Today the user
must re-read the new `IP:port` off the phone each time (hit **three times** during companion
bring-up). This is the single biggest remaining friction in the whole workflow.

**Existing machinery** (`src/phonectl/connection.py`): `rediscover()` tries `last_port` /
`serial` then mDNS; `last_port` is persisted. mDNS is the weak link.

**Candidate approaches (to weigh in a brainstorm):**
- **(a) Pin a stable port via one-time USB `adb tcpip <fixed-port>`** — most robust; survives
  rotation, but requires an initial USB connection.
- **(b) Persistent auto-rediscover** on every command (retry `last_port`, then a bounded scan),
  with clear backoff.
- **(c) A `phonectl reconnect` command** that re-pairs/re-connects interactively and updates
  `last_port` — one guided step instead of manual config editing.
- **(d) Ephemeral-range port scan** fallback when mDNS fails (slower, last resort).

**Recommendation:** brainstorm → spec → plan. This is a real feature with a design fork
(stable-port vs. rediscover-harder), not a doc edit. `phonectl companion setup` currently
*assumes* a live adb connection and only surfaces `device offline`, deferring to this work.

**Acceptance:** after a port rotation, `phonectl` reconnects without the user manually reading
the port off the phone (or with a single guided command).

## 3. Approach A — phonectl-minted **pushed token** for release builds — v2, **needs an APK/Kotlin change**

The shipped token model is **B (run-as, debug builds) + C (prompt, fallback)**. The end-user
design for *release* builds — where `run-as` is denied — is **Approach A**: phonectl mints the
shared secret and pushes it to the companion at first pair, so there is zero manual token copy.

**Design sketch (needs a spec):** phonectl generates a token and sends it via a first-pair
broadcast the companion adopts. The wrinkle: `LifecycleReceiver` currently *requires* the
existing token to authorize (Finding 14), so the first-pair must be a **trust-on-first-use**
path valid only when no token is yet set — a deliberate security decision to design carefully.

**Blocked on:** an Android build/test loop (Kotlin change). **Acceptance:** on a release-build
companion, `phonectl companion setup` pairs with no manual token step.

## 4. Kotlin Finding-5 remediation — companion caps default-**disabled** — **needs an APK/Kotlin change**

**Gap discovered 2026-07-02:** `Capabilities.DEFAULT_ENABLED = true`
(`android/accessibility-companion/.../state/Capabilities.kt:52`) was **never flipped**, so the
companion still ships **every** capability enabled by default. Only the *Python* half of
adversarial-review Finding 5 landed (`mode: confirm`; `trust.gate_capabilities` defaults
unknown keys off). The review's remediation note over-claims Finding 5 as fully fixed.

**Fix:** flip `DEFAULT_ENABLED` to `false`; default the sensitive caps (`set_text`,
`notifications_reply`, `ocr`, `screencap`) disabled; update `SharedPrefsTrustState` defaults and
`CapabilitiesTest`. Also correct the remediation note in
`docs/adversarial-review-2026-07.md` (Finding 5) to state the companion half is now done.

**Blocked on:** an Android build (JVM/Robolectric test). **Acceptance:** a fresh-install
handshake shows sensitive caps default **off**; the user opts in per-capability in the app UI.

## 5. Companion-setup Minor cleanup (SDD final-review triage) — tiny, no behavior change

Minors triaged as acceptable-to-defer during the companion-setup SDD run (all in the PR #45
diff):
- `AdbBackend.run_adb` lacks a `-> subprocess.CompletedProcess` return annotation (siblings are
  annotated); a test has a redundant local `import subprocess`.
- `companion_setup.parse_token` / `read_token_via_runas` return annotations are quoted
  `"str | None"` — redundant under `from __future__ import annotations`.
- `ensure_accessibility` idempotency-skip test covers only exact-match, not
  component-among-others (the substring behavior is covered elsewhere; test gap only).
- `acquire_token` prompt-fallback test asserts the in-memory `cfg` only, not a `config.load()`
  round-trip (the run-as test does the round-trip).
- `verify` empty-caps success message renders `"reachable; 0 caps: "` (trailing colon).
- `config set` unknown-key CLI test asserts the exit code only, not the printed error message.

**Acceptance:** one cleanup commit addressing the above; behavior unchanged; suite still green.

---

## Suggested sequencing

1. **Item 1** (on-device validation) as soon as a device is reachable — cheap, de-risks the
   whole feature.
2. **Item 2** (port-rotation) next — it's the highest-leverage friction and gates comfortable
   on-device iteration for everything else. Brainstorm first.
3. **Items 3 & 4** together once an Android build loop exists (both are Kotlin/APK work).
4. **Item 5** whenever touching `companion_setup.py` next (fold into another PR).
