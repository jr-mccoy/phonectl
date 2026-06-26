# `generated/` — rebuildable projections (NOT source of truth)

Everything in this directory is a **projection** rebuilt from the canonical records
elsewhere in `.project-memory/`. Never edit these by hand and never treat them as
authoritative — if a projection disagrees with the canonical records, the records
win and the projection should be regenerated.

| File | Built by | What it is |
|---|---|---|
| `resume-packet.md` | `crumb resume` | Bounded boot summary (3k–5k tokens) for pasting into any agent. |
| `stale-report.md` | `crumb audit` | Computed staleness / risk findings. |
| `memory-index.md` | `crumb audit` / index build | Human-readable index of records. |

Each projection carries a source timestamp/commit/hash header so staleness is
visible. By default these `*.md` files are committed (so a read-only cloud agent
gets a pre-built catch-up file); set `commit_generated_projections: false` at `init`
to keep them local instead. `*.local.md` and `*.tmp` here are always gitignored.

SQLite and vector indexes never live here — they live in `index/`, which is always
gitignored.
