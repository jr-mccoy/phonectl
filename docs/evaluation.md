# Evaluation

A test suite answers "does this function do what I said". It does not answer "would an agent
driving this actually succeed, and how often does it need a human". Those are different
questions, and the second one is the one that decides whether droidjig is useful.

`eval/` is the harness for the second question.

```console
$ python -m eval
scenario                 pass   actions  success  p50 ms   interventions
------------------------------------------------------------------------
settings_nav             PASS   1        1.00     1.8      0
form_fill_unicode        PASS   1        1.00     1.1      0
notification_otp         PASS   1        1.00     0.9      0
messaging_dry_run        PASS   1        0.00     0.0      1
list_extraction_dedup    PASS   1        1.00     1.2      0
recovery_drill           PASS   1        0.00     0.1      0
safety_drill             PASS   1        0.00     0.3      0
------------------------------------------------------------------------
7/7 scenarios passed
```

It exits non-zero if any scenario fails, so it can gate CI. It needs no device.

## Read that output carefully

Three scenarios show `success 0.00` and every scenario shows `PASS`. That is not a
contradiction, and the distinction is the whole point of the harness.

- **`success_rate`** measures whether the *action* succeeded.
- **`passed`** measures whether droidjig did the *right thing*.

`messaging_dry_run` sends a reply in `confirm` mode without `--yes`. The correct behavior is to
refuse and hand off to a human, so the action fails (`success 0.00`), one intervention is
recorded, and the scenario passes because refusing was correct. `safety_drill` taps "Pay now" on
a payment screen with `--yes` already given: the risk classifier must rate it `critical` and the
policy must deny it anyway. A `success_rate` of 1.00 there would be a failing grade.

An eval that only measured success rate would reward an agent for doing the dangerous thing.
This one grades the refusals as first-class outcomes, which is why the safety scenarios are in
the same harness as the happy paths rather than in a separate "safety tests" bucket that nobody
reads.

## What is measured

`eval/metrics.py` derives five numbers from the structured `results.ok` / `results.err`
envelope — never from stdout, so the metrics are stable across the CLI, MCP, and daemon
frontends:

| Metric | What it tells you |
|---|---|
| `success_rate` | Fraction of actions whose envelope came back `ok`. |
| `median_latency_ms` | p50 wall-clock per action. Median, not mean: one slow reconnect should not repaint the whole picture. |
| `stale_target_rate` | Fraction that failed with `stale_snapshot` — the agent acted on an element index from a screen that had already changed. This is *the* characteristic failure of index-based targeting, so it gets its own number. |
| `provider_fallback_count` | How often the registry walked down the provider stack — the companion was unavailable and ADB served the call instead. Rising fallbacks mean the fast path is quietly degrading. |
| `human_interventions` | Actions that stopped at a `confirmation_required` gate. This is the autonomy number: how much of the work the agent could not finish alone. |

## What is exercised

Seven scenarios, in `eval/scenarios.py`:

| Scenario | What it proves |
|---|---|
| `settings_nav` | Launch an app, find a row by selector, tap it, land on the next screen. The basic observe→act→observe loop. |
| `form_fill_unicode` | Type `Café 日本語 😀 Ω` into a field and verify it arrives intact. Text input is where encoding assumptions go to die. |
| `notification_otp` | Pull a verification code out of notification text and type it into the code field — the single most-requested phone-agent task. |
| `messaging_dry_run` | A reply in `confirm` mode without `--yes` must be refused and must never reach the device. Asserts `b.texts == []`: not just that it reported a refusal, but that nothing was typed. |
| `list_extraction_dedup` | Scroll a list and extract unique rows. Rows repeat across scroll positions; the extraction must deduplicate rather than double-count. |
| `recovery_drill` | A locked device must produce a typed error envelope with `requires_user` set, not a crash or a traceback. |
| `safety_drill` | A payment screen must classify `critical` and be denied by policy even when `--yes` was passed, with zero taps delivered. |

Scenarios 1–5 are happy paths and the safety hand-off; 6–7 are adversarial probes.

## How it works

Every scenario drives the **real** `runtime.run_action` pipeline. Nothing is stubbed out between
the scenario and the choke-point, so the mode gate, kill switch, risk classification, policy,
rate limit, idempotency, and audit log all run exactly as they do in production. That is what
makes the numbers meaningful: they measure the shipped path, not a parallel one built for
testing.

What *is* faked is the phone. `eval/simulator.py`'s `ScriptedBackend` implements the
`backend.Backend` protocol over a fixed list of UI screens: navigational actions advance a
cursor to the next screen, text and key input are recorded but do not advance, and
`ui_dump` / `window_dump` / `wm_size` are served from the current screen. No ADB, no companion,
no device.

`eval/harness.py` meters each call and isolates state — `isolated_home()` gives a standalone run
a fresh `DROIDJIG_HOME` in a temp directory, so a benchmark can never touch your real config,
audit log, or rate-limit history.

The same scenarios also run under pytest (`tests/test_eval_suite.py`), which is what keeps them
from rotting.

## Adding a scenario

1. Write the screens with `screen(...)` and `node(...)` from `eval.simulator`.
2. Build a `ScriptedBackend` over them and a `Harness`.
3. Drive the action through `h.run(verb, fn, target, build=..., yes=...)` — never call the
   actuator directly, or the metrics will not reflect the funnel.
4. Compute `passed` from the *envelope and the backend state*, not from the envelope alone. The
   safety scenarios assert `b.taps == []` and `b.texts == []` because "reported a refusal" and
   "did not do the thing" are different claims, and only the second one matters.
5. Add it to `SCENARIOS`.

## What this does not prove

The simulator is a model of a phone, not a phone. It cannot tell you that a vendor ROM renames
the keyguard string, that a WebView returns an empty tree, or that a `swipe` on a real
`RecyclerView` overshoots. **Unit and eval coverage prove logic, not topology.** Device
behavior is only proven by the manual matrix in [integration-smoke.md](integration-smoke.md),
and the project does not claim otherwise anywhere.

The honest summary: this harness tells you the pipeline is correct and the safety gates hold. It
does not tell you the agent will succeed on your phone.
