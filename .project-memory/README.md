# `.project-memory/` — Project Continuity Memory

This directory is a repo-local, human-readable ledger of durable project state for
human–agent software work. It is managed by [`breadcrumbs`](https://github.com/jumbodaddystack/breadcrumbs)
but is **plain files first**: any human or agent can read it without the tool.

> **Memory is advisory.** Current user instruction, source code, tests, build
> output, current authoritative docs, and security policy outrank anything here. If
> memory conflicts with reality, it is marked `disputed` or `stale` — it does not
> override the present.

---

## Project memory protocol (for agents and humans)

**Before non-trivial work:**

1. Read `current.md` — what matters right now.
2. Read `handoff.md` — what to do first.
3. Review relevant records under `decisions/`, `attempts/`, `known-traps.md`, and
   `open-questions.md`.
4. If the CLI is available, run `crumb guard "<proposed action>"`.

**At session end:**

1. Run `crumb capture session` (or write a session record by hand).
2. Record durable decisions and failed attempts as typed records.

A read-only cloud agent with no CLI can resume from these files directly:
`current.md`, `handoff.md`, `decisions/`, `attempts/`, `known-traps.md`,
`open-questions.md`, and `generated/resume-packet.md`.

---

## What lives where

| Path | Contents |
|---|---|
| `manifest.yml` | Schema version + tracking policies chosen at `init`. |
| `current.md` | What matters right now (days to ~2 weeks). |
| `handoff.md` | What the next session should do first. |
| `open-questions.md` | Unresolved ambiguities / blockers. |
| `known-traps.md` | Reusable warnings about fragile areas. |
| `decisions/` | One record per durable decision (`YYYY-MM-DD-slug.md`). |
| `attempts/` | One record per tried path + outcome + do-not-retry. |
| `sessions/` | One record per work session. |
| `ideas/` | Potential future directions. |
| `evidence/refs.yml` | Pointers to commits/tests/docs/issues/PRs. |
| `generated/` | Rebuildable projections — **not source of truth**. |
| `private/` | Local-only notes — **never committed**. |
| `index/` | Disposable search index — **never committed** (except this kind of README). |

Source-of-truth and status rules live in the tool's `docs/` (architecture,
record-schema, security).
