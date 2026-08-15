# droidjig — Companion Startup (`droidjig companion setup`) Design

**Date:** 2026-07-02
**Status:** Approved design (brainstorming); implementation plan to follow.
**Author:** Jeremy McCoy (with Claude)
**Scope decision:** Companion **bring-up** only. ADB connection stability (Wireless-Debugging
port rotation, dead mDNS, reconnect) is a *separate* connection-layer concern with its own
partial machinery (`connection.rediscover()`) and gets its **own follow-up plan** — see
[Out of Scope](#out-of-scope--follow-ups).

---

## Problem

Standing up the companion so `droidjig` can drive it currently takes **many manual,
error-prone round-trips**. Observed first-hand on a Samsung Galaxy S25 Ultra (2026-07-02):

1. **Install** the debug APK (`adb install`).
2. **Enable the AccessibilityService** — no droidjig command; requires `adb shell settings put
   secure enabled_accessibility_services …` or menu-diving.
3. **Start the socket server** — the foreground service does **not** auto-start on this build;
   the only starts are the emergency-stop toggle or a token-authenticated `am broadcast`. This
   alone cost ~6 diagnostic steps to discover.
4. **Pair the token** — shown in the app UI, hand-copied into config. A fresh install mints a
   **new** token, so this repeats on every reinstall.
5. **`POST_NOTIFICATIONS`** is ungranted on a fresh install → the FGS notification is silently
   suppressed.
6. **No orchestration** — `droidjig setup` (`setup.py`) covers ADB pairing and prints
   *informational* module reports only (`_MODULE_META`); it performs none of the above.
7. **No `droidjig config set`** — the token had to be written via a raw Python one-liner.

Net: bring-up is undocumented tribal knowledge, and the reinstall loop (core to companion
development) is painful.

## Goals

- **One idempotent command** brings the companion from "APK on disk" to "droidjig verified it
  end-to-end": `droidjig companion setup`.
- **No token copy-paste** on debug builds.
- **Safe-by-default preserved** — powerful grants (accessibility, starting a remote-control
  socket) require explicit confirmation and are printed before they happen. "Seamless" =
  *one command + one informed confirmation*, never silent auto-arming.
- **droidjig-only, stdlib-only** — no Android/Kotlin change, so it is fully testable in this
  repo (no Android build needed).
- **Re-runnable** — every step detects "already done" and skips; the reinstall loop becomes a
  single repeatable command.

## Non-Goals

- Fixing ADB port rotation / reconnection (separate plan).
- Release-build token automation (see approach A, deferred).
- Fetching the APK from CI. v1 takes a local path / auto-detects a download.
- Changing the companion APK in any way.

---

## Approach

Everything in bring-up is mechanical `adb` **except** obtaining the token. That is the only
real design fork. Decision:

**Primary — (B) read the companion's token via `run-as`.** Debug APKs are debuggable, so
`adb shell run-as com.phonectl.companion cat shared_prefs/droidjig_companion.xml` succeeds and
exposes `<string name="companion_token">…</string>`. droidjig parses it, no user involvement.

**Fallback — (C) guided prompt.** When `run-as` is denied (release build, or run-as
unavailable), the command opens the app, tells the user where the token is, and prompts for it
— today's flow, but wrapped and one-shot.

**Deferred — (A) droidjig-generated pushed token.** droidjig mints the secret and pushes it to
the companion at first pair. This is the *right* end-user design for release builds (no
run-as), but it requires an **APK/Kotlin change** plus a trust-on-first-use decision
(unauthenticated first-pair, since `LifecycleReceiver` currently requires the existing token to
authorize). Out of scope for v1; recorded as the natural v2.

**Why B is correct here.** `run-as` requires adb + the debuggable flag; droidjig already **is**
the adb-trusted party, so reading the token via run-as discloses it to no one who isn't already
in control. It needs no Android build — which matters because Kotlin cannot be compiled/tested
in this repo. It fails closed to C on release builds.

> **De-risking note:** run-as readability was source-confirmed (`PREFS = "droidjig_companion"`,
> `KEY_TOKEN = "companion_token"` in `SharedPrefsTrustState.kt`) but not yet live-validated
> (the device dropped its Wireless-Debugging port mid-check — the very issue deferred to the
> connection plan). The implementation plan's **first task is a device spike** confirming the
> run-as read before building on it.

---

## Architecture

New stdlib module **`src/droidjig/companion_setup.py`**, mirroring `setup.py`'s dependency-
injection style (`setup.py` already threads `prompt`, `out`, `which`, `exists` seams for
device-free unit tests). Every step is a **small pure-ish function** taking injectable seams so
it unit-tests without a device.

### Injectable seams

- `adb(*args) -> str` — run an `adb -s <serial> …` command, return stdout (raises on nonzero).
  Wraps the existing `AdbBackend._adb`; **all** device contact goes through it (backend
  isolation invariant).
- `run_as(rel_path) -> str | None` — `adb shell run-as <pkg> cat <rel_path>`; `None` on denial.
- `prompt`, `out` — I/O seams (as in `setup.py`).

### Steps (each independently testable)

| Step | Function | Idempotency check | Needs `--yes`? |
|---|---|---|---|
| 1 | `ensure_installed(adb, apk_path)` | `pm list packages` has `com.phonectl.companion` **and** installed hash == apk hash | no (install is benign) |
| 2 | `ensure_accessibility(adb)` | `settings get secure enabled_accessibility_services` already contains the component | **yes** |
| 3 | `ensure_notifications(adb, out)` | `POST_NOTIFICATIONS` already granted (`dumpsys package`) | no |
| 4 | `acquire_token(run_as, prompt, cfg)` | `cfg["companion_token"]` already present **and** validates against a live handshake | no |
| 5 | `start_server(adb, token)` | socket `:8765` already `LISTEN` | **yes** |
| 6 | `verify(cfg) -> report` | — (always runs; it is the "done" signal) | no |

- **Step 1** auto-detects the newest `app-debug.apk` under `~/Download` and `/sdcard/Download`
  when `--apk` is omitted; on a signature-mismatch reinstall it uninstalls first (debug keys
  differ across CI runs), warning that this resets the token + grants.
- **Step 3** does what adb can (`pm grant POST_NOTIFICATIONS`) and *prints* the one step it
  cannot (the notification-listener toggle, which has no secure-settings equivalent), reusing
  `setup._MODULE_META["notifications"]` guidance text.
- **Step 4** parses `shared_prefs/droidjig_companion.xml` for `companion_token` (B); on `None`
  from `run_as`, launches the app and prompts (C). Persists via the new `config set` path.
- **Step 5** fires the token-authenticated `START_SERVICE` broadcast
  (`am broadcast -a com.phonectl.companion.action.START_SERVICE --es token <t> -n …/.service.LifecycleReceiver`),
  then polls `ss -tln` for `:8765` up to a timeout.
- **Step 6** runs `trust.negotiate` over a token'd `SocketTransport` and prints
  `reachable`, `stopped`, and the capability list — identical to the manual verification used
  during design.

### Orchestrator

`run_companion_setup(adb, run_as, cfg, *, apk_path=None, assume_yes=False, prompt, out) -> int`
sequences steps 1→6, short-circuiting each on its idempotency check, gating steps 2 & 5 behind
`assume_yes` (printing the grant first, then confirming unless `--yes`). Returns a
`_do_action`-style exit code (0 ok; nonzero per failing step).

### CLI surface

- `droidjig companion setup [--apk PATH] [--yes] [--json]` → `run_companion_setup`.
- `droidjig companion status [--json]` → steps' idempotency checks as a readout (installed?
  accessibility bound? socket up? token paired? handshake caps) — folds today's ad-hoc
  diagnostics into one command.
- `droidjig config set <key> <value>` / `droidjig config get <key>` — real config CLI (fixes
  friction #7; `get` partly exists). `set` validates known keys against `config.DEFAULTS`.

`companion` becomes a subparser group alongside `setup`, mirroring the existing `clipboard` /
`packages` / `notifications` group pattern in `cli.py`.

---

## Data flow

```
droidjig companion setup --apk app-debug.apk
  └─ run_companion_setup
       1 ensure_installed ─ adb pm list / install ────────────► package present
       2 ensure_accessibility ─ [--yes] settings put secure ──► service bound
       3 ensure_notifications ─ pm grant + print manual step ─► FGS notif allowed
       4 acquire_token ─ run_as cat prefs.xml  (B) ───────────► token
                        └─ else launch app + prompt (C)          │
                           config set companion_token <token> ◄──┘
       5 start_server ─ [--yes] am broadcast START_SERVICE ───► :8765 LISTEN
       6 verify ─ trust.negotiate(token) ─────────────────────► caps report → exit 0
```

## Error handling

- Any step failure prints a specific remediation and returns nonzero **without** proceeding
  (fail-closed): missing adb → `setup.INSTALL_GUIDANCE`; device offline → point at the
  connection plan / `droidjig setup`; run-as denied → drop to prompt (not an error); socket
  never comes up → surface the FGS-start hint and `companion status`.
- Uninstall-on-reinstall (signature mismatch) is announced before it wipes token + grants.
- `--json` emits a structured per-step result envelope (`results.ok`/`results.err`) so an agent
  or the daemon can drive setup and branch on outcomes.

## Safety

- Steps 2 and 5 are the only powerful actions; both are `--yes`-gated and print exactly what
  they grant/start first. This upholds the safe-by-default posture (Finding 5 ethos) rather
  than bypassing it.
- The command does **not** enable any sensitive *capability* — those remain the companion's own
  toggles (and remain default-on until the separately-tracked Kotlin Finding-5 gap is fixed;
  see follow-ups). Setup only wires the transport + starts the server.
- No new code path bypasses `runtime.run_action`; setup issues device *administration* commands
  (install/grant/broadcast/observe), not gated *actions*.

## Testing (TDD, non-negotiable)

- **Unit** — each step function against a fake `adb`/`run_as` that records issued commands and
  returns canned stdout: installed-vs-missing, hash-match skip, accessibility already-set skip,
  run-as success **and** denial→prompt, socket-up poll success/timeout, verify caps parsing.
  `DROIDJIG_HOME`-isolated, no device.
- **Fixture** — a real `droidjig_companion.xml` sample drives the token parser; a
  parametrized case covers a blank/absent token entry.
- **CLI** — `droidjig companion setup/status` and `config set/get` argparse wiring + exit codes
  via the existing CLI test harness.
- **Device spike (plan task 1, manual)** — confirm the run-as read on a debug build before the
  parser is built on it.

---

## Out of scope / follow-ups

1. **ADB connection stability** — Wireless-Debugging port rotation + dead mDNS + reconnect.
   Own plan. Candidate directions: one-time USB `adb tcpip <fixed-port>`, persistent
   `rediscover()` on every command, a `droidjig reconnect`. This design *assumes* a live adb
   connection and points at that plan on `device offline`.
2. **Approach A (pushed token)** — release-build token automation via a droidjig-minted secret
   pushed at first pair; requires an APK/Kotlin change + a TOFU first-pair path. Natural v2 once
   an Android build loop exists.
3. **Kotlin Finding-5 gap** — `Capabilities.DEFAULT_ENABLED = true` was never flipped; the
   companion still ships all capabilities enabled by default (the Python half of Finding 5
   landed, the companion half did not). Independent of setup; file as its own remediation.
