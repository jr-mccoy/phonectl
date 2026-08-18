# droidjig

[![Python tests](https://github.com/jr-mccoy/phonectl/actions/workflows/python.yml/badge.svg)](https://github.com/jr-mccoy/phonectl/actions/workflows/python.yml)
[![Android companion APK](https://github.com/jr-mccoy/phonectl/actions/workflows/android.yml/badge.svg)](https://github.com/jr-mccoy/phonectl/actions/workflows/android.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

**Give an AI agent a real Android phone, safely.** droidjig turns the screen into structured JSON
an agent can reason about, lets it act on that screen by element rather than by pixel, and puts
every action through one auditable safety gate. No root, no cloud, no vendor SDK — just `adb`.

```bash
# Observe: the screen as indexed elements
$ droidjig observe --json | jq '.data.elements[1:3] | map({i, text, id, clickable, bounds})'
[
  { "i": 1, "text": "Wi-Fi",     "id": "android:id/title", "clickable": true, "bounds": [44, 380, 1036, 520] },
  { "i": 2, "text": "Bluetooth", "id": "android:id/title", "clickable": true, "bounds": [44, 540, 1036, 680] }
]

# Act: by element, not by pixel
$ droidjig tap --index 1
$ droidjig tap --text "Wi-Fi"
$ droidjig tap --selector '{"text_regex":"^(Wi-?Fi|Bluetooth)$","clickable":true}'

# Every action re-observes, so the loop can tell whether it landed.
```

## Why it is built this way

Three decisions carry the whole design.

**Target elements, not coordinates.** `tap --index 1` survives a different screen size, a
different launcher, and a vendor ROM that moved the row down forty pixels. `tap 540 450` does
not. Pixel-driven agents look fine in a demo and break on contact with a second device.

**Re-observe after every act.** Every actuator returns the post-action snapshot, and the change
in `screen_hash` is the evidence that the action landed. Without it, an agent is writing to a
device it cannot read — and will confidently narrate work that never happened.

**One choke-point, no second path.** The thing on the other end is an autonomous agent driving a
real phone, so every action — from the CLI, from MCP, from a macro, from the daemon — funnels
through `runtime.run_action`, which applies the mode gate, kill switch, risk classification,
policy, rate limit, idempotency, and audit log. The daemon reuses that function *verbatim*, so
the safety properties are identical in-process and over the wire. A safety control you can route
around is a safety control you do not have.

> **Safety.** This tool grants an AI agent real control of a real phone. The default action mode
> is `confirm` — every action asks first. Read [the safety model](docs/safety.md) before
> switching to `auto`, and read the [security review](docs/adversarial-review-2026-07.md) before
> any unattended use. The project's honest position is that it is built for *supervised* agent
> use.

## Quickstart

```bash
# 1. Install adb (Termux: pkg install android-tools · Debian: apt install android-tools-adb)

# 2. Install droidjig
git clone https://github.com/jr-mccoy/phonectl.git && cd phonectl
pip install -e .

# 3. Pair with the phone — Settings > Developer options > Wireless debugging
droidjig setup          # interactive: pairs, connects, verifies, saves the config

# 4. Confirm it worked
droidjig doctor
# droidjig: connected (serial=127.0.0.1:PORT, state=device)

# 5. Drive it
droidjig observe --json
droidjig launch com.android.settings
droidjig tap --text "Wi-Fi" --yes
```

Full instructions, including manual pairing and the `adb`-missing path, are in
[docs/install.md](docs/install.md).

## What you get

| | |
|---|---|
| **[Commands](docs/cli-reference.md)** | `observe`, `tap`, `type`, `swipe`, gestures, `key`, `launch`, `clipboard`, `device`, `tts`, `intent`, `packages`, `wait-for`, `doctor`, `mcp` — plus JSON selectors and structured extraction (list rows, form fields, screen regions). |
| **[Safety model](docs/safety.md)** | Three action modes, the risk ledger and policy, guarded apps, the audit log, the kill switch, and exit codes that distinguish "declined" from "broke". |
| **[Providers](docs/providers.md)** | ADB always; the AccessibilityService companion APK, Termux:API, and OCR when present. The registry picks per capability, degrades cleanly, and reports which provider actually served each call. |
| **[Daemon](docs/daemon.md)** | Optional single-writer runtime with async jobs, durable run records, and an event bus. The primitives work with or without it. |
| **[Macros](docs/macros.md)** | Declarative JSON automations with control flow, triggers, and schedules — every step still inside the funnel — plus revocable autonomy grants and redacted memory stores. |
| **[Evaluation](docs/evaluation.md)** | `python -m eval` scores agent behavior on seven scenarios: success rate, median latency, stale-target rate, provider fallbacks, and human interventions. |
| **[Configuration](docs/configuration.md)** | The result envelope, the config keys, connection recovery, and performance tuning. |
| **[MCP](docs/cli-reference.md#mcp)** | `pip install droidjig[mcp]` exposes the same envelopes to an MCP client. |

## Status

**Shipped and unit-tested (879 tests, 83% coverage):** structured results and capabilities;
selector and tree observation; resilience and the setup wizard; the `run_action` single-writer
funnel with risk policy and audit v2; the provider/capability graph (clipboard, intents,
packages, gestures, extraction, Termux:API); the companion APK and its event providers
(accessibility, notifications, OCR); the daemon and event runtime; and the macro engine with
progressive autonomy. Core behavior is validated on a real Samsung Galaxy S25 Ultra over
Wireless Debugging.

**Next:** a Shizuku provider, to reduce the dependence on an ADB connection.

**Caveats worth knowing.** Unit tests run against injected fakes — they prove the logic, not the
device topology. The real-device connectivity proof and the end-to-end smoke matrix are manual
steps needing a physical Android 11+ phone with Wireless Debugging enabled; see
[docs/integration-smoke.md](docs/integration-smoke.md). The Android instrumented tests
(`connectedAndroidTest`) do not run in CI. Nothing has been released yet — no tag and no PyPI
package — though publishing a GitHub release now builds the companion and attaches the APK to
it automatically; until one is published the APK is a CI artifact only. Phase status lives
in [docs/roadmap.md](docs/roadmap.md); security posture and remaining risk in
[docs/adversarial-review-2026-07.md](docs/adversarial-review-2026-07.md).

## Development

```bash
git clone https://github.com/jr-mccoy/phonectl.git
cd phonectl
pip install -e ".[dev]"     # package + console script + pytest
pip install -e ".[dev,mcp]" # also the optional FastMCP transport

pytest -v                                               # full suite (~4s)
pytest tests/test_ui_parser.py -v                       # one file
pytest tests/test_ui_parser.py::test_parse_bounds -v    # one test
python -m eval                                          # the agent benchmark
```

The suite is 879 tests, all against injected fakes — no device required. One test needs the
optional `mcp` extra and skips without it; one file-permission test skips when the suite runs as
root. Tests marked `device` need real hardware over Wireless Debugging and are excluded in CI
(`pytest -m "not device"`).

The Android companion APK lives in `android/accessibility-companion/` and builds independently:

```bash
cd android/accessibility-companion
./gradlew assembleDebug test
```

**Before changing a core layer, read [`docs/architecture.md`](docs/architecture.md).** The
invariants there are load-bearing — in particular, only `adb_backend.py` may call `adb`, and
every action must go through `runtime.run_action`. Tests come first: write the failing test,
confirm it fails for the right reason, then write the code that passes it.
[`CONTRIBUTING.md`](CONTRIBUTING.md) has the rest.

## How this was built

droidjig was written almost entirely through AI pair-programming, under a discipline meant to
make that trustworthy: tests before code, invariants written down before they could be violated,
and review commissioned *against* the work rather than in praise of it. The process — including
what the AI got wrong and how those failures were caught — is in
[docs/built-with-ai.md](docs/built-with-ai.md).

## Documentation

The [docs index](docs/README.md) lists everything: the architecture invariants, one design note
per subsystem, the adversarial security review, the whole-system audit, and the on-device smoke
matrix.

## License

[MIT](LICENSE) © Jeremy McCoy
