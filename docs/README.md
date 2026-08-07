# phonectl documentation

Start with the [README](../README.md) for install, the command reference, and the safety model.
These docs cover the reasoning underneath it.

## Design

| Document | What it covers |
|---|---|
| [architecture.md](architecture.md) | The load-bearing invariants — backend isolation, the `runtime.run_action` choke-point, re-observe after every act — and why each one exists. **Read this before changing a core layer.** |
| [strategy.md](strategy.md) | The product argument for evolving phonectl from an observe/act bridge into a local Android automation platform for agents. |
| [roadmap.md](roadmap.md) | Phase-by-phase status: what is built, what is next, what is deliberately deferred. |
| [design/](design/) | One design note per subsystem, written before the code: the ADB bridge, resilience, the daemon and its async-job model, the event runtime, the macro engine, idempotency and cache eviction, companion startup, and pushed-token v2 pairing. |

## Reviews and findings

| Document | What it covers |
|---|---|
| [adversarial-review-2026-07.md](adversarial-review-2026-07.md) | A self-commissioned adversarial security review — 16 findings with `file:line` evidence, all since remediated, plus the residual risk that remains. |
| [capability-test-findings-2026-07-04.md](capability-test-findings-2026-07-04.md) | A manual end-to-end capability sweep against a real device, and the two reproducible bugs it surfaced. |
| [system-improvement-audit-2026-07-09.md](system-improvement-audit-2026-07-09.md) | An audit of how well the system serves an autonomous agent, and where the leverage is. |

## Guides

| Document | What it covers |
|---|---|
| [setup-walkthrough.md](setup-walkthrough.md) | Getting connected the first time. |
| [integration-smoke.md](integration-smoke.md) | The manual on-device smoke matrix — what unit tests structurally cannot prove. |
| [macros.md](macros.md) | The macro schema, control-flow steps, and the progressive-autonomy and memory model. |
