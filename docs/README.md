# droidjig documentation

Start with the [README](../README.md) for the pitch and a quickstart. These docs are the
reference underneath it.

## Using droidjig

| Document | What it covers |
|---|---|
| [install.md](install.md) | Installing `adb` and droidjig, pairing over Wireless Debugging, and diagnosing a connection that will not come up. |
| [setup-walkthrough.md](setup-walkthrough.md) | What `droidjig setup` does, prompt by prompt, including the branch where `adb` is missing. |
| [cli-reference.md](cli-reference.md) | Every command and flag, the MCP tool surface, JSON selector syntax, and structured extraction. |
| [safety.md](safety.md) | The three action modes, the risk ledger and policy, guarded apps, the audit log, the kill switch, and the exit codes. |
| [providers.md](providers.md) | The provider graph and its selection rules, plus the Termux:API, AccessibilityService companion, and OCR providers and their trust controls. |
| [daemon.md](daemon.md) | The single-writer daemon: async jobs, run records, the event bus, and what still works without it. |
| [macros.md](macros.md) | The macro schema, the control-flow step catalogue, and the progressive-autonomy and memory model. |
| [evaluation.md](evaluation.md) | The benchmark harness — what the seven scenarios test, what the five metrics mean, and how to add a scenario. |
| [configuration.md](configuration.md) | The result envelope, capability discovery, config keys, connection recovery, and performance tuning. |

## Design

| Document | What it covers |
|---|---|
| [architecture.md](architecture.md) | The load-bearing invariants — backend isolation, the `runtime.run_action` choke-point, re-observe after every act — and why each one exists. **Read this before changing a core layer.** |
| [design/](design/) | One design note per subsystem, written before the code: the ADB bridge, resilience, the daemon and its async-job model, the event runtime, the macro engine, idempotency and cache eviction, companion startup, and pushed-token v2 pairing. |
| [built-with-ai.md](built-with-ai.md) | How the project was actually built with AI, what the discipline was, and what the AI got wrong. |
| [strategy.md](strategy.md) | *(Internal planning.)* The product argument for evolving droidjig from an observe/act bridge into a local Android automation platform for agents. |
| [roadmap.md](roadmap.md) | *(Internal planning.)* Phase-by-phase status: what is built, what is next, what is deliberately deferred. |

## Reviews and findings

| Document | What it covers |
|---|---|
| [adversarial-review-2026-07.md](adversarial-review-2026-07.md) | A self-commissioned adversarial security review — 16 findings with `file:line` evidence, all since remediated, plus the residual risk that remains. |
| [audit-2026-08-15.md](audit-2026-08-15.md) | A whole-system audit against two questions: is this defensible as a portfolio project, and what stands between it and real users. Scorecard, six defects, and a ranked action list. |
| [capability-test-findings-2026-07-04.md](capability-test-findings-2026-07-04.md) | A manual end-to-end capability sweep against a real device, and the two reproducible bugs it surfaced. |
| [system-improvement-audit-2026-07-09.md](system-improvement-audit-2026-07-09.md) | An audit of how well the system serves an autonomous agent, and where the leverage is. |
| [integration-smoke.md](integration-smoke.md) | The manual on-device smoke matrix — what unit tests structurally cannot prove. |
