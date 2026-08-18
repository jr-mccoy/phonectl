# Safety model

droidjig gives an AI agent real control of a real phone. Everything here describes one
funnel — `runtime.run_action` — that every action in every frontend passes through, and the
controls layered into it.

Read the [adversarial security review](adversarial-review-2026-07.md) before any unattended
use. The project's honest position is that it is built for *supervised* agent use.

---

## Three action modes

The `mode` key in `~/.config/droidjig/config.json` (or `$DROIDJIG_HOME/config.json`) controls how action verbs behave:

| Mode | Behaviour |
|---|---|
| `auto` | Acts immediately. Opt-in: set it explicitly in `config.json`. |
| `confirm` | Prints the intended action and refuses unless `--yes` is passed on the command line. Exit code `3` on refusal. **Default when no mode is set.** |
| `dry-run` | Observes the screen, prints what would have been done, but does **not** inject any input and does **not** write to the audit log. |

Set the mode by editing `config.json`:
```json
{ "mode": "confirm" }
```

Then pass `--yes` to action verbs to permit execution in confirm mode:
```bash
droidjig tap --index 3 --yes
droidjig type "hello" --yes
```

## Audit log

Every executed action (tap, type, swipe, key, launch) is appended to `~/.config/droidjig/actions.jsonl` as a JSONL record containing the timestamp, verb, target, resulting foreground app, screen hash, and `outcome` (`ok` or `blocked`). Dry-run actions are not logged.

## Single-writer runtime & audit

All mutating action verbs route through `runtime.run_action`, the single writer for UI changes. The funnel applies the kill switch and mode checks, serializes concurrent writers — both within a process (thread lock) and across droidjig processes (an `flock` on `$DROIDJIG_HOME/action.lock`) — stamps each call with a `request_id`, executes the action, re-observes, and writes the audit record.

Action verbs accept `--json` to print the full structured result envelope:

```bash
droidjig tap --xy 100 200 --json
droidjig type "hello" --request-id req-123 --idempotency-key msg-1 --json
```

The action envelope includes `verb`, `target`, and `request_id`. A repeated `--idempotency-key` replays the first envelope with `idempotent_replay: true` instead of executing the action again. Replay envelopes persist to `$DROIDJIG_HOME/idempotency.json` (TTL `idempotency_ttl`, default 300 s), so deduplication holds across one-shot CLI invocations, not just within the daemon process.

Single-writer control errors use stable codes:

| Code | Exit | Meaning |
|---|---:|---|
| `busy` | `1` | Another action (in this or another droidjig process) holds the single-writer lock; retry later. |
| `stopped` | `2` | The `STOP` kill-switch file is present. |
| `confirmation_required` | `3` | Confirm mode refused the action because `--yes` was not supplied. |

## Risk ledger & policy

Before any mutating action executes, `runtime.run_action` observes the current screen and classifies the pending action as `low`, `medium`, `high`, or `critical`. The classifier reads the foreground package and parsed UI element metadata; it does not call adb directly.

| Signal | Level | Trigger |
|---|---|---|
| `guarded_package` | `high` | Foreground package starts with a configured guarded prefix. |
| `password_field` | `high` | A parsed element has `password: true`. |
| `payment_keyword` | `critical` | Screen text contains payment/purchase/bank/card wording. |
| `destructive_keyword` | `critical` | Screen text contains factory reset, wipe, delete account, or uninstall wording. |
| `install_keyword` | `high` | Screen text contains install or sideload wording. |
| `otp_like_content` | `medium` | Visible element text contains a 4-8 digit code. |
| `high_risk_verb` | `high` | Verb is `packages_stop`, `intent_start`, `intent_broadcast`, or `notifications_reply`. |
| `critical_verb` | `critical` | Verb is `packages_clear`. |
| `critical_intent` | `critical` | `intent_start` action/data targets the dialer or SMS (`tel:`, `sms:`/`smsto:`/`mms:`, `ACTION_CALL`/`DIAL`/`SENDTO`). |

Effective default policy:

```json
{
  "risk_policy": {
    "low": "allow",
    "medium": "allow",
    "high": "confirm",
    "critical": "deny"
  },
  "guarded_packages": [],
  "rate_limits": {
    "tap": 120,
    "type": 30,
    "swipe": 120,
    "key": 120,
    "launch": 20,
    "high_risk": 1,
    "global": 180
  }
}
```

The `risk_policy` values are `allow`, `confirm`, or `deny`. A risk-policy `confirm` returns `confirmation_required` unless the action is re-run with `--yes`; `deny` returns `guarded_action`. Successful action envelopes and policy/rate failures include `risk_level` and `reasons`.

Rate limits are bucketed sliding windows over the last minute. Every allowed action counts against `global` and its verb bucket; `high` and `critical` actions also count against `high_risk`. Rate state is stored in `$DROIDJIG_HOME/ratelimit.json`. A limit breach returns `rate_limited` with `bucket`.

Use `policy explain` to inspect a decision before acting:

```bash
droidjig policy explain --verb tap --text "Pay now" --json
```

```json
{
  "risk_level": "critical",
  "reasons": [{"signal": "payment_keyword", "detail": "screen text matches payment_keyword"}],
  "decision": "deny",
  "recommended_action": "blocked by policy; override risk_policy to permit"
}
```

Set `audit_level` in `config.json` to control audit detail:

| Level | Behavior |
|---|---|
| `none` | Write no audit record. |
| `metadata` | Write timestamp, verb, request ID, resulting app, and hash only. |
| `redacted` | Default. Write metadata plus a redacted target. |
| `full` | Write the raw target. |

Redaction scrubs OTP-like numeric codes, email addresses, phone numbers, card-like numbers, and URL query secrets such as `token=`, `access_token=`, `code=`, and `key=`. Non-sensitive selectors such as `{"text": "Wi-Fi"}` are left unchanged.

Audit inspection commands:

```bash
droidjig audit tail --limit 20
droidjig audit export audit.json
droidjig audit export audit-full.json --no-redact
droidjig audit purge
```

## Kill switch

Engage the kill switch to instantly refuse all action verbs regardless of mode. Any of these work:

```bash
droidjig stop                    # engage (host CLI)
touch ~/.config/droidjig/STOP    # engage (or create the sentinel directly)
```

Clearing it is a **human-only, out-of-band** step — there is no agent-facing resume (no
`phone_resume` MCP tool, no daemon `resume` RPC), so an agent cannot turn its own brakes off:

```bash
droidjig resume                  # disengage (host CLI, human action)
rm ~/.config/droidjig/STOP       # or remove the sentinel directly
```

`droidjig stop`/`resume` are host commands; the agent's only reachable control is engaging
`STOP` (via `phone_stop` or the daemon `stop` RPC). Any action verb while `STOP` is present
exits with code `2` and prints:
```
droidjig: action refused (kill switch STOP present)
```

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Timeout (`wait-for`), connection error, policy denial, rate limit, or busy writer |
| `2` | Kill switch active, or `wait-for` called without `--text`/`--id` |
| `3` | Confirm-mode refusal (action verb called without `--yes`) |
| `4` | Internal error — a bug in droidjig, not a refusal. Reported as an `internal_error` envelope; set `DROIDJIG_DEBUG=1` to re-raise and get the traceback |
| `130` | Interrupted with Ctrl-C (128 + SIGINT) |

Codes `1`–`3` mean droidjig worked and declined; `4` means droidjig itself broke. A closed
pipe (`droidjig observe --json | head`) exits `0`.
