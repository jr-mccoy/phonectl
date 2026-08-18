# droidjig command reference

Every command, its flags, and the shape it returns. Actions are gated by the action mode —
the default is `confirm`, so action verbs need `--yes` or a `mode: auto` config. See
[safety.md](safety.md) for the gate, the risk ledger, the kill switch, and the exit codes.

---

## Command reference

All subcommands connect to the device using the serial stored in `~/.config/droidjig/config.json` (override with `DROIDJIG_HOME`).

### `observe`

Dump the current screen as a structured JSON snapshot: foreground app, screen dimensions, a hash of the element set, and a flat list of UI elements with indices, text, resource IDs, bounds, and click-targets.

```bash
droidjig observe
droidjig observe --screenshot                  # also capture a PNG
droidjig observe --screenshot-path /tmp/snap.png
```

### `tap`

Tap an element by its index from the last `observe` output (preferred — device-size-agnostic), or by raw coordinates.

```bash
droidjig tap --index 7
droidjig tap --xy 540 450
```

### `type`

Type text into the focused field.

```bash
droidjig type "hello world"
```

### `swipe`

Swipe from (X1, Y1) to (X2, Y2), or in a named direction with density-aware scaling.

```bash
droidjig swipe 540 1600 540 400          # coordinate form: scroll up
droidjig swipe up                        # named direction (full screen)
droidjig swipe down --within i=3         # scroll within container element 3
droidjig swipe left --distance-pct 0.7  # custom distance
```

### Gestures

High-level gesture verbs built on ADB's `input swipe` primitive.

**Named swipe** (density-aware — coordinates computed from `wm size`):
```bash
droidjig swipe up|down|left|right [--within i=N] [--distance-pct 0.5]
```

**Long-press** (zero-distance swipe held for `--duration-ms`):
```bash
droidjig long-press --i N [--duration-ms 1000]
droidjig long-press --x X --y Y
```

**Double-tap** (two taps separated by `--interval-ms`):
```bash
droidjig double-tap --i N [--interval-ms 100]
droidjig double-tap --x X --y Y
```

**Drag** (long-duration swipe — portable ADB drag primitive; `adb shell input draganddrop` is not universally available):
```bash
droidjig drag --x1 X --y1 Y --x2 X --y2 Y [--duration-ms 500]
```

**Fling** (velocity-scaled fast swipe):
```bash
droidjig fling up|down|left|right
```

**Scroll** (container-aware; reads element bounds from snapshot when `--within` is set):
```bash
droidjig scroll down [--within i=N]
droidjig scroll up --within i=5
```

**Scroll-until** (observe→scroll loop; returns the snapshot in which the target appeared, or the last snapshot if `--max` scrolls are exhausted):
```bash
droidjig scroll-until --text "Advanced" [--direction down] [--within i=N] [--max 10]
droidjig scroll-until --selector '{"resource_id":"com.example:id/item"}' --max 5
```

All gesture verbs route through `runtime.run_action` (kill switch, mode, audit, risk policy all apply). Use `--json` for structured output and `--yes` in confirm mode.

### `key`

Send a key event. Friendly names accepted: `back`, `home`, `recents`, `enter`. Any raw Android keycode string also works.

```bash
droidjig key back
droidjig key home
droidjig key enter
droidjig key KEYCODE_VOLUME_UP
```

### `launch`

Start an app by package name using the monkey launcher-intent mechanism (`monkey -p <pkg> -c android.intent.category.LAUNCHER 1`). The package must expose a LAUNCHER activity.

```bash
droidjig launch com.android.settings
droidjig launch com.android.chrome
```

### `clipboard`

Read, write, or clear the system clipboard.

```bash
droidjig clipboard read               # requires Termux:API (Plan 3.5)
droidjig clipboard write "hello"      # via ADB service call (Android 10+)
droidjig clipboard write "hello" --yes
droidjig clipboard clear --yes
```

**Note:** `clipboard read` is not available via plain ADB because the parcel-based read is ROM-specific and unreliable. It returns a `capability_unavailable` error with install instructions until Termux:API is configured (`droidjig setup termux-api`).

`clipboard write` and `clipboard clear` are mutating operations that route through `runtime.run_action` (audit log, risk policy, kill switch, mode gates all apply).

### `device`

Read device state via the Termux:API provider (requires `droidjig setup termux-api`).

```bash
droidjig device battery          # Battery percentage, status, health, temperature
droidjig device battery --json   # Structured result envelope
droidjig device wifi             # WiFi SSID, IP, BSSID, RSSI
droidjig device wifi --json
```

Returns `capability_unavailable` with install instructions if Termux:API is not configured.

### `tts`

Speak text via Android's TTS engine (requires `droidjig setup termux-api`).

```bash
droidjig tts speak "Hello, world"
droidjig tts speak "Bonjour" --language fr
droidjig tts speak "Fast" --rate 1.5
droidjig tts speak "Hello" --json   # Structured result envelope
```

TTS is fire-and-forget: the command returns as soon as the TTS engine accepts the request. The speech plays asynchronously. Returns `capability_unavailable` if Termux:API is not configured.

### `intent`

Start activities or send broadcasts via `am start` / `am broadcast`.

```bash
droidjig intent start --action android.intent.action.VIEW --data "geo:37.422,-122.084"
droidjig intent start --component com.android.settings/.wifi.WifiSettings --yes
droidjig intent broadcast com.example.MY_ACTION --yes
droidjig intent broadcast com.example.MY_ACTION --extra key=value --yes
```

Intent `start` and `broadcast` are **high-risk** operations (risk level `high`) and require `--yes` or explicit policy override. The risk ledger classifies both `intent_start` and `intent_broadcast` as `high_risk_verb`; an `intent start` whose action or data targets the dialer or SMS (`tel:`, `sms:`/`smsto:`, `ACTION_CALL`/`DIAL`/`SENDTO`) is classified **critical** and denied by default policy. Multiple `--extra K=V` pairs are supported (string extras only; typed extras are deferred).

### `packages`

List, inspect, launch, stop, or clear packages.

```bash
droidjig packages list                          # user-installed packages only
droidjig packages list --all                    # include system packages
droidjig packages resolve com.android.settings  # version, launch activity
droidjig packages launch com.android.settings   # same as `launch` verb
droidjig packages stop com.example.app --yes    # force-stop (high risk)
droidjig packages clear com.example.app --yes   # clear data (critical risk)
```

Risk levels:
- `packages list` / `packages resolve` — read-only; no risk gate.
- `packages launch` — same risk classification as the `launch` verb.
- `packages stop` — **high risk** (`high_risk_verb` signal); requires `--yes` or a `high: allow` policy override.
- `packages clear` — **critical risk** (`critical_verb` signal); requires `--yes` and is **denied by default policy** — override `risk_policy.critical` to `confirm` first.

### `wait-for`

Re-observe on a short poll until a UI element matching `--text` or `--id` appears, or until `--timeout` seconds elapse. Requires exactly one of `--text` or `--id`.

```bash
droidjig wait-for --text "Network & internet" --timeout 8
droidjig wait-for --id "android:id/title" --timeout 5
```

Exit codes: `0` on match, `1` on timeout, `2` if neither `--text` nor `--id` is provided.

### `doctor`

Check device connectivity, print structured JSON, or create a redacted diagnostics bundle.

```bash
droidjig doctor
droidjig doctor --json
droidjig doctor --bundle /tmp/droidjig-diag.zip
# droidjig: connected (serial=127.0.0.1:PORT, state=device)
```


### `mcp`

Launch the optional stdio MCP server so agents can call droidjig as native tools. The live transport needs the optional MCP SDK; handlers and tests remain stdlib-only.

```bash
pip install 'droidjig[mcp]'
droidjig mcp
```

Every MCP tool returns the same structured result envelope used by `--json` CLI actions. Agents should branch on `ok`, `error.code`, `requires_user`, `risk_level`, and `reasons` instead of parsing human text.

Tool catalog:

| Tool | Purpose | Key args |
|---|---|---|
| `phone_observe_ui` | Return the foreground UI snapshot. | `tree`, `relations`, `screenshot` |
| `phone_find` | Resolve a selector against a fresh observation. | `selector` |
| `phone_capabilities` | Report backend capability flags and summary text. | none |
| `phone_tap` | Tap by selector, index, or coordinates via `run_action`. | `selector`, `index`, `x`, `y`, `expected_hash`, `stale_ok`, `dry_run`, `confirm`, `reason`, `idempotency_key` |
| `phone_type` | Type into the focused field via `run_action`. | `text`, `dry_run`, `confirm`, `reason`, `idempotency_key` |
| `phone_swipe` | Swipe between points via `run_action`. | `x1`, `y1`, `x2`, `y2`, `dry_run`, `confirm` |
| `phone_key` | Send a key event via `run_action`. | `keycode`, `dry_run`, `confirm` |
| `phone_launch` | Launch a package via `run_action`. | `package`, `dry_run`, `confirm` |
| `phone_policy_explain` | Explain risk and policy before acting. | `verb`, `selector`, `index`, `x`, `y` |
| `phone_audit_query` | Read recent redacted audit entries. | `limit` |
| `phone_stop` | Engage the emergency stop. | none |
| `phone_clipboard_read` | Read clipboard text (requires Termux:API). | none |
| `phone_clipboard_write` | Write text to the clipboard. | `text`, `dry_run`, `confirm` |
| `phone_clipboard_clear` | Clear the clipboard. | `dry_run`, `confirm` |
| `phone_intent_start` | Start an activity via `am start`. | `action`, `data`, `component`, `extras`, `dry_run`, `confirm` |
| `phone_intent_broadcast` | Send a broadcast via `am broadcast`. | `action`, `extras`, `dry_run`, `confirm` |
| `phone_packages_list` | List installed packages. | `include_system` |
| `phone_packages_resolve` | Resolve package metadata (version, launch activity). | `package` |
| `phone_packages_launch` | Launch a package. | `package`, `dry_run`, `confirm` |
| `phone_packages_stop` | Force-stop a package (high risk). | `package`, `dry_run`, `confirm` |
| `phone_packages_clear` | Clear package data (critical risk; set `confirm=true`). | `package`, `confirm`, `dry_run` |
| `phone_named_swipe` | Swipe in a named direction with density-aware scaling. | `direction`, `distance_pct`, `ms`, `within_index`, `dry_run`, `confirm` |
| `phone_long_press` | Long-press by index, selector, or coordinates. | `index`, `selector`, `x`, `y`, `duration_ms`, `dry_run`, `confirm` |
| `phone_double_tap` | Double-tap by index, selector, or coordinates. | `index`, `selector`, `x`, `y`, `interval_ms`, `dry_run`, `confirm` |
| `phone_drag` | Drag from (x1,y1) to (x2,y2) via long-duration swipe. | `x1`, `y1`, `x2`, `y2`, `duration_ms`, `dry_run`, `confirm` |
| `phone_fling` | Fling in a direction with velocity-scaled speed. | `direction`, `dry_run`, `confirm` |
| `phone_scroll` | Scroll in a direction, optionally within a scrollable container. | `direction`, `within_index`, `distance_pct`, `dry_run`, `confirm` |
| `phone_scroll_until` | Scroll until text or selector appears or max_scrolls is exhausted. | `direction`, `text`, `selector`, `max_scrolls`, `within_index` |
| `phone_notifications_list` | List current notifications; each item includes `can_reply`/`can_dismiss` flags. | `package` |
| `phone_notifications_wait` | Poll until a matching notification appears or timeout elapses. | `package`, `title_contains`, `text_contains`, `timeout` |
| `phone_notifications_reply` | Reply to a notification via RemoteInput (**high-risk**; companion required). | `key`, `text`, `confirm`, `dry_run` |
| `phone_notifications_dismiss` | Dismiss a notification (companion required). | `key`, `confirm`, `dry_run` |
| `phone_ocr_screen` | OCR the current screen and return text regions with bounds and confidence. **Use only as a fallback** when `phone_observe_ui`/`phone_find` return nothing (canvas/WebView/game surfaces). Requires `tesseract` on PATH or the companion ML-Kit OCR provider. | `min_confidence` |
| `phone_extract_list` | Extract rows from a scrollable list container. | `container_index` |
| `phone_extract_form` | Extract form fields with associated labels. | none |
| `phone_get_focused_field` | Return the currently focused text field, or null. | none |
| `phone_find_text` | Find elements whose text matches a regex pattern (`re.search`). | `pattern` |
| `phone_macro_validate` | Validate a macro document; returns `{valid, errors}`. | `macro` |
| `phone_macro_run` | Run a declarative macro document; returns the run envelope. | `macro`, `yes` |
| `phone_macro_status` | List recent macro runs from `runs.jsonl`. | `limit` |

Example observe envelope:

```json
{"ok": true, "capability": "ui.observe", "provider": "adb", "data": {"elements": []}}
```

Example blocked action envelope:

```json
{
  "ok": false,
  "error": {"code": "confirmation_required", "requires_user": true},
  "verb": "tap",
  "risk_level": "high",
  "reasons": ["password_field"]
}
```

The action tools are thin frontends over `runtime.run_action`, so MCP and CLI use the same single-writer lock, kill switch, dry-run mode, confirmation policy, rate limits, re-observe-after-act behavior, and audit log.

There is deliberately **no `phone_resume` tool** (and no daemon `resume` RPC). Engaging the
kill switch (`phone_stop`) is agent-reachable, but *clearing* it is a human-only, out-of-band
action — a kill switch the agent can turn off is not a kill switch. Resume from the host with
`droidjig resume`, by removing the `STOP` sentinel, or from the companion notification/tile.

### Global flag

```bash
droidjig --version
```

---

---

## Selector targeting and tree observation

`droidjig` supports durable selector-based targeting in addition to snapshot-local element indices and raw coordinates. Selectors are JSON objects whose present keys all match: `text`, `text_regex`, `content_desc`, `resource_id`, `class`, boolean element flags such as `clickable`, `enabled`, `checked`, `editable`, relation predicates `ancestor_text` and `sibling_text`, `bounds_near` (`[x1,y1,x2,y2]` center-in-box), and `nth_match` (zero-based pick after ranking).

Examples:

```bash
droidjig tap --text "Wi-Fi"
droidjig tap --id android:id/title --nth 1
droidjig tap --selector '{"text_regex":"^(Wi-?Fi|Bluetooth)$","clickable":true}'
droidjig observe --tree --relations
```

Use `--expected-hash HASH` on actions to prevent acting when the observed screen has changed. If the current hash differs, `droidjig` re-observes once and raises the typed `stale_snapshot` error unless `--stale-ok` is supplied, in which case it proceeds against the fresh snapshot.

---

## Structured extraction

Read structured data from the UI — enumerate RecyclerView rows, extract form labels and values, locate the focused text field, filter elements by text pattern, or extract all visible text within a region.

```bash
# Extract all rows from a scrollable list (auto-detects the container)
droidjig extract list --json

# Extract rows from a specific container by element index
droidjig extract list --container-i 3 --json

# Extract form fields with associated labels
droidjig extract form --json

# Find elements whose text matches a regex (UI tree)
droidjig find --text-regex "Total|Balance" --json

# Find text by OCR when the UI tree returns nothing (canvas/game/WebView surfaces)
droidjig find --ocr-text "Balance" --json

# Get the currently focused text field
droidjig get focused-field --json

# Get all elements overlapping a screen region (x1 y1 x2 y2)
droidjig get text-in-region --bounds 0 0 1080 400 --json
```

`extract form` automatically requests the UI relations graph to resolve label–field associations via sibling proximity; if no relations are available it falls back to Y-coordinate overlap. Password fields have their value replaced with `[redacted]` in all outputs.

`find --text-regex` searches element text only (uses `re.search`, so no anchoring needed). For `content-desc` matching use `--selector '{"content_desc": "..."}'` with `observe` or `tap`.

`find --ocr-text REGEX` is the escape hatch for surfaces the UI tree can't see: it OCR-scans the screen and filters regions whose text matches the regex. Requires the [OCR provider](providers.md#ocr-provider-optional).

---
