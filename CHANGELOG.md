# Changelog

All notable changes to droidjig are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project intends to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) from 1.0.0 onward.

**Nothing has been released yet.** No git tag and no PyPI package exist; `version = "0.1.0"` in
`pyproject.toml` is the in-development version. Everything below is what is on `master`, grouped
so the first release notes can be lifted from it directly.

## [Unreleased]

### Added

**Observe → act core.** `observe` returns the screen as indexed JSON elements (text, resource id,
content description, bounds, and the clickable/enabled/scrollable/editable flags) plus a
`screen_hash`. `tap`, `type`, `swipe`, `key`, and `launch` act by element index, by text, or by
selector rather than by pixel, and every action re-observes so the caller can tell whether it
landed. Gestures (long-press, double-tap, drag, named swipes, fling, scroll, scroll-until), `wait-for`, `clipboard`, `device`,
`tts`, `intent`, `packages`, and `doctor` round out the command surface.

**Selector targeting and structured extraction.** JSON selectors match on text, regex, resource
id, class, package, and element flags. Higher-level extraction reads list rows (with
deduplication across scrolls), form fields with their labels, the focused text field, and every
element overlapping a screen region.

**The safety funnel.** Every action in every frontend passes through `runtime.run_action`, which
applies, in order: the action-mode gate (`confirm` by default, plus `auto` and `dry-run`), the
kill switch, risk classification, the policy decision, the rate limit, the idempotency check,
and the append-only audit log in `actions.jsonl`. Guarded apps can be listed in config, which
refuses actions *and* observation, screenshots, and OCR against them. Exit codes distinguish
"droidjig declined" from "droidjig broke".

**Provider graph.** A `ProviderRegistry` picks the best provider per capability and degrades
cleanly when one is absent, reporting truthfully which provider satisfied each call. Providers:
ADB, the AccessibilityService companion, Termux:API, notifications, clipboard, intents,
packages, and OCR.

**Android companion APK** (`com.droidjig.companion`, Kotlin). An AccessibilityService plus a
foreground service, talking to the Python side over an authenticated loopback socket. It
supplies a native view tree with stable node ids, semantic tap and long-press, screenshots as
base64 PNG, ML-Kit OCR, notification list/reply/dismiss, and an on-device emergency stop with a
Quick-Settings tile. Trust is established by pushed-token v2 (trust-on-first-use pairing), and
every capability is an individually revocable toggle that is off unless granted.

**Daemon.** `droidjig daemon` is the single writer: the CLI auto-routes actions to it over
loopback JSON-RPC when it is running, and it reuses `runtime.run_action` verbatim so the safety
properties are identical in-process and over the wire. It adds an async job model, durable run
records in `runs.jsonl`, an event bus with a poller, and snapshot caching. The primitives keep
working with no daemon at all.

**Macro engine.** Declarative JSON macros with variables and interpolation, control flow
(`if` / `switch` / `for_each` / `loop` / `retry` with backoff and re-check / `try` / `confirm`),
a trigger vocabulary, schedules, per-macro fire limits, and cancellation. Every macro action
still goes through the funnel, with `parent_task_id` linking it to its run.

**Progressive autonomy and memory.** An autonomy ledger grants specific macros unattended
execution up to a stated risk ceiling, revocably and with expiry. Redacted memory stores learn
selectors from successes and count retryable failures; the user can show, export, and delete
them.

**MCP server.** An optional FastMCP transport (`pip install droidjig[mcp]`) exposing the same
structured-result envelopes as the CLI.

**Evaluation suite.** `python -m eval` runs seven benchmark scenarios — navigation, Unicode form
fill, OTP capture, list extraction, a safety hand-off, and two adversarial probes — against a
scripted backend, and reports success rate, median latency, stale-target rate, provider-fallback
count, and human interventions. It runs the real `runtime.run_action` pipeline and needs no
device.

**Self-healing connection.** Wireless-debug reconnection with a port-scan fallback, and
`ensure()` auto-recovery on a dropped connection.

### Changed

- Renamed the Python package and the Android companion from `phonectl` to `droidjig`
  (`com.phonectl` → `com.droidjig`). The `phonectl` console script and `$PHONECTL_HOME` /
  `~/.config/phonectl` still work so existing installs keep their paired companion token; both
  are removable at 1.0.
- Dropped Python 3.9. `requires-python` is now `>=3.10`, which is what the code had always
  required in practice — the metadata simply now says something true. CI runs 3.10, 3.11, 3.13.
- Made the defaults safe: `mode: confirm`, and unknown companion capabilities disabled rather
  than enabled.

### Fixed

Sixteen findings from the self-commissioned adversarial security review
([`docs/adversarial-review-2026-07.md`](docs/adversarial-review-2026-07.md)), all remediated —
notably the agent's ability to clear its own emergency stop, unauthenticated local transports,
a fail-open companion kill-switch path, `scroll_until` bypassing the choke-point, unquoted
intent arguments, and process-local idempotency that did not hold across one-shot CLI
invocations.

Four defects from the whole-system audit
([`docs/audit-2026-08-15.md`](docs/audit-2026-08-15.md)): non-atomic state-file writes that a
mid-write interruption turned into a permanently broken install (D1), unexpected errors escaping
as tracebacks instead of structured envelopes (D2), non-constant-time token comparison on both
the Python and Kotlin sides (D3), and a CI lane that had been red long enough to hide a second,
deeper failure behind it (D6).

### Performance

Round trips to the device dominate latency, so most of the work went there: `observe` rides one
combined dump instead of two round trips; `wm_size` is cached with a TTL and invalidated on
serial change; companion connections persist with cached liveness; the per-action companion
stop-check transport is memoized; unsupported keycodes are detected before the RPC rather than
after; daemon job polling ramps from 50 ms rather than sitting at the poll interval; and an
opt-in `action_observe_ttl` lets an action reuse a sufficiently fresh pre-action snapshot.

### Known limitations

- No release artifacts: no tag, no PyPI package, and the companion APK is a CI build artifact
  rather than a GitHub release asset.
- The Kotlin companion is compiled and JVM-unit-tested in CI, but CI runs no emulator, so
  runtime behavior is proven only by the manual on-device matrix in
  [`docs/integration-smoke.md`](docs/integration-smoke.md).
- No human-only sensitive-action policy layer yet. droidjig is built for **supervised** agent
  use; unattended autonomy is out of scope until that lands.
- `cli.py` is the largest and least-covered module (69% against a suite averaging 83%).
