# Built with AI

droidjig was written almost entirely through AI pair-programming, over roughly two months and
about 150 commits. Commit authorship splits close to evenly between the AI and the maintainer,
and essentially all of the Python and Kotlin was drafted by the model.

That fact is worth stating plainly, because "AI wrote it" is not by itself a claim about
quality. It is a claim about *method*, and the method is the part that either holds up or does
not. This document describes what the method was, what it caught, and what it missed.

## The problem with AI-written code

An LLM will produce plausible code for almost any request, and it will produce plausible
*documentation* for that code in the same breath. The two failure modes that matter are:

1. **It writes code that looks right and is subtly wrong** — the kind of wrong that passes a
   test written by the same model in the same session, because the test encodes the same
   misunderstanding.
2. **It writes documentation that describes the code it meant to write**, not the code it
   wrote. This one is more dangerous, because docs are what a reader trusts when they are not
   reading source.

Both of these happened in this project. The rest of this document is mostly about how they were
found.

## The method

**A standing instruction file.** [`CLAUDE.md`](../CLAUDE.md) sits at the repo root and is loaded
into every session. It is deliberately short: it points at the architecture invariants rather
than restating them, names the working discipline (tests first, one logical change per commit,
conventional-commit subjects), and carries the two rules most likely to be violated by a model
optimizing for a passing test — never bypass `runtime.run_action`, and never claim device
behavior that has not been run on a device. Keeping it small is the point; a long instruction
file is one the model skims.

**Invariants written down before they could be violated.**
[`docs/architecture.md`](architecture.md) states the load-bearing properties — backend
isolation, the choke-point, re-observe after every act, all state through `state.py` — as rules
with reasons attached. An invariant with a stated reason survives a refactor; a convention does
not. When the audit later found that one of these invariants was written more strictly than the
code actually enforced, the *invariant text* was corrected rather than quietly ignored, because
a rule the codebase knowingly violates trains everyone to skip the whole list.

**A design note per subsystem, in the repo.** [`docs/design/`](design/) holds one note each for
the ADB bridge, resilience, the daemon and its async-job model, the event runtime, the macro
engine, idempotency and cache eviction, companion startup, and pushed-token v2 pairing. Writing
the note first forces the interesting decisions — what the seam is, what degrades when a
provider is absent — to be made in prose, where they are cheap to change, rather than in code,
where they are not.

**Tests in the same commit as the behavior.** At the time of writing, of the non-merge
commits that touch Python source, **69 of 72 also touch tests**. The three that do not are a
whitespace refactor, a release-prep chore, and a URL fix. The suite is hermetic — no device, no
network, no real `adb`, everything behind an injected `runner=` — which is why it runs in about four seconds, and the
speed is what kept it in the loop rather than in CI-only purgatory.

Git cannot prove that the failing test was written *before* the code inside a single commit. It
can prove the test exists and that it constrains the behavior. Take the discipline claim for
what the evidence supports.

## What the AI got wrong

Three separate reviews were commissioned against the work, each with a different adversarial
posture. None of them were asked to confirm that the project was good.

### The security review — 16 findings

[`docs/adversarial-review-2026-07.md`](adversarial-review-2026-07.md) is a security review of
the AI-written code, with `file:line` evidence and severity ranking. It found, among other
things, that **the agent could clear its own emergency stop**, that both local control surfaces
were unauthenticated, that the companion's kill-switch path was fail-open, and that
`scroll_until` reached the device through a path that skipped the choke-point the entire design
rests on.

That last one is the instructive failure. The invariant was written down. The code was reviewed.
And a convenience wrapper still went around it, because going around it was the shortest path to
a working feature and nothing mechanically prevented it. All 16 findings were fixed; the
document stays in the repo, residual-risk section included.

### The documentation gap — 11 mismatches

The same review contains a section titled *Docs vs Implementation Mismatches*, and it is the
most useful artifact in the project for anyone thinking about AI-generated content. Eleven
places where the prose and the code disagreed:

- The README rated `intent start` as **high** risk. `risk.py` rated it **low**.
- The README said the kill switch "blocks all immediately". Three separate paths did not honor
  it.
- The README presented "loopback only" as a security guarantee. On Android, loopback is not a
  UID boundary, and there was no authentication behind it.
- The README described the single-writer lock as general. It was process-local, so it did not
  hold across one-shot CLI invocations at all.
- The README said password fields are redacted to `[redacted]`. The companion set them to `""`.
- The README's Status section still listed shipped subsystems as "deferred".
- `CLAUDE.md` claimed 579 tests. There were 586.

Every one of these is the same failure: **the documentation described the intended system, and
the code implemented a slightly different one.** No individual claim was a lie, and each was
plausible enough to survive a read-through. They were only caught by a pass whose specific job
was to diff the prose against the source.

This is the thing to take seriously about AI-written docs. The model is not careless about
truth; it is optimizing for a coherent description, and a coherent description of a system is
not the same as an accurate one. Coherence is not a proxy for correctness, and reviewing
AI-written prose *as prose* will never catch it — the only thing that works is checking each
claim against the artifact it describes.

### The device sweep — what unit tests structurally cannot see

[`docs/capability-test-findings-2026-07-04.md`](capability-test-findings-2026-07-04.md) records
a manual sweep of the whole CLI against a real Samsung Galaxy S25 Ultra. Most of the surface
worked. Two reproducible bugs did not, and the first is a nice specimen:

`droidjig autonomy grant <macro> --expires N` treated `N` as an absolute Unix timestamp instead
of "N seconds from now". The CLI passed `args.expires` straight into a function whose parameter
was already absolute-epoch. Both halves were individually correct and internally consistent, and
the unit tests on each half passed. The grant was simply dead on arrival — it expired
approximately 56 years before it was created.

Nothing hermetic catches that. It is a units mismatch across a seam, and seams are exactly where
a model's local reasoning stops.

### The whole-system audit — six defects

[`docs/audit-2026-08-15.md`](audit-2026-08-15.md) graded the repository against two questions:
is this defensible as a portfolio project, and what stands between it and real users. It found
that state files were written non-atomically, so an interrupted write — `kill -9`, a dead
battery, a full disk, all routine on a phone — left truncated JSON that permanently bricked
every command including `doctor`, the one command whose job is diagnosing a broken install.

It also found that **Python 3.9 had never worked at all.** `requires-python` said `>=3.9`, the
README carried a 3.9 badge, and CI ran a 3.9 lane. The lane was red, and had been for a while,
because dependency resolution failed at the *install* step — which meant pytest never ran, which
meant nobody saw the 19 collection errors hiding behind it. The support claim had been false the
whole time, and one red signal was masking a second, deeper one.

The lesson generalizes past this project: a check that has been failing for a while is not
noise. It is a place where you have stopped receiving information.

## What actually worked

Three things, in order of how much they mattered:

**A single choke-point beats many checks.** `runtime.run_action` is one function that applies the
mode gate, kill switch, risk classification, policy, rate limit, idempotency, and audit log. The
daemon reuses it verbatim rather than reimplementing the sequence, so the safety properties are
identical in-process and over the wire. Concentrating enforcement in one place makes "did we
enforce this?" a question with a single answer — and makes a bypass, when one appears, a
findable bug rather than a diffuse one.

**Hermetic and fast beats thorough and slow.** Four seconds means the suite runs on every
change, which is why it stayed healthy across the project's whole history. A twenty-minute suite gets run at PR
time, which is far too late to shape the code.

**Reviews commissioned against the work, not for it.** The prompt matters enormously. "Review
this code" produces a list of compliments with three nits at the end. "Find what an attacker
gets, rank by severity, cite `file:line`" produces sixteen findings. The adversarial framing is
not a stylistic preference — it changes what the model looks for, and therefore what it finds.

## What is still open

Stated here rather than buried, because a document about honesty that ends on a high note is
doing the exact thing it warns about:

- **`cli.py` is around 1,950 lines at 69% coverage**, against a suite averaging 83%. It is the largest
  module and the surface every user touches. It should be split into a command package.
- **CI runs no emulator.** The Kotlin companion is compiled and JVM-unit-tested, but its runtime
  behavior is proven only by a manual on-device matrix. Every device claim in these docs traces
  to one phone, one Android version, one ROM.
- **Nothing has been released.** No tag, no package, and the companion APK is a CI artifact.
- **There is no human-only sensitive-action policy layer.** Until there is, droidjig is built
  for supervised agent use, and the docs say so everywhere rather than in a footnote.

## The short version

The interesting output of this project is not the feature list. It is the discipline that made
AI-written code trustworthy enough to hand a real phone to: invariants written down before they
could be violated, one enforcement point instead of many, a test suite fast enough to actually
run, and reviews whose job was to find what was wrong rather than to confirm what was right.

The reviews found real problems. That is the evidence that the method worked — not the absence
of findings, but the presence of them, and the fact that they are still in the repository.
