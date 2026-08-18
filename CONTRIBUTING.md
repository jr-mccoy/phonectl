# Contributing to droidjig

Thanks for looking. droidjig gives an AI agent real control of a real phone, so the bar for
changes is a little higher than usual in one specific way: **the safety funnel and the
architecture invariants are load-bearing**, and a change that breaks one is a bug even if the
test suite stays green.

## Before you write code

Read [`docs/architecture.md`](docs/architecture.md). It is short, and it is the contract. The
two rules that matter most:

1. **Only `adb_backend.py` may call `adb`.** Everything else speaks the `backend.Backend`
   Protocol or the `ProviderRegistry`. Providers that shell out to non-`adb` tools do so behind
   an injected `runner=` seam, so tests never spawn a process.
2. **Every action goes through `runtime.run_action`.** That one function applies the mode gate,
   kill switch, risk classification, policy, rate limit, idempotency, and audit log. There is no
   second path, and adding one is the change most likely to be rejected.

If you are reworking a subsystem, the design note for it is in [`docs/design/`](docs/design/) —
one per subsystem, written before the code.

## Development setup

```bash
git clone https://github.com/jr-mccoy/droidjig.git
cd droidjig
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,mcp,yaml]"
```

Python 3.10+ is required. The runtime itself is stdlib-only; `mcp` and `PyYAML` are optional
extras and `pytest` is dev-only. **Adding a hard runtime dependency needs an explicit reason** —
this thing runs inside Termux on a phone, where every install step is a place users drop off.

## Tests

```bash
pytest -v                            # the whole suite (~4s)
pytest -m "not device"               # what CI runs
pytest tests/test_ui_parser.py -v    # one file
python -m eval                       # the seven-scenario agent benchmark
```

**Tests come first.** Write the failing test, run it, confirm it fails *for the reason you
expect*, then write the minimum code that passes it. The suite is the specification; a PR whose
tests were written after the code is usually visible as one, and will be asked for a rewrite.

Three things about the suite are deliberate and should stay true:

- **It is hermetic.** No device, no network, no real `adb`. Everything injects a fake `runner=`
  or a fake transport. This is why it runs in four seconds and why it stayed healthy.
- **It proves logic, not topology.** Unit tests cannot prove device behavior. Anything
  ROM-specific — port recovery, keyguard strings, the companion transport, OCR — is only truly
  verified by the on-device matrix in [`docs/integration-smoke.md`](docs/integration-smoke.md).
- **Tests that exercise actions must opt into `mode: auto` explicitly.** The default mode is
  `confirm`, and it stays that way in tests so nothing accidentally proves the gate is absent.

Tests marked `device` need real hardware over Wireless Debugging and are excluded from CI.

## Commits and pull requests

- **One logical change per commit**, with a [conventional-commit](https://www.conventionalcommits.org/)
  subject: `feat:`, `fix:`, `docs:`, `perf:`, `test:`, `refactor:`, `chore:`, plus a scope where
  one applies (`fix(daemon):`). Breaking changes take a `!`.
- The body should say *why*, not restate the diff. If the change touches a safety path, say what
  the failure mode was.
- Keep the PR to the change it describes. Drive-by refactors in an unrelated file make review
  slower and bisects worse.
- CI must be green on Python 3.10, 3.11, and 3.13.

## Documenting device behavior

**Never claim device behavior that has not been run on a device.** This is the project's oldest
rule and the reason its docs can be trusted. Where a claim is unvalidated, say so in the text.
If you verify something on real hardware, add it to
[`docs/integration-smoke.md`](docs/integration-smoke.md) with the device and Android version.

## Cutting a release

Releases are maintainer-only, and the APK attaches itself — do not upload one by hand.

1. Move the `## [Unreleased]` section of [`CHANGELOG.md`](CHANGELOG.md) under a new version
   heading, and open a fresh `## [Unreleased]` above it.
2. Set the version in `pyproject.toml`, and `versionName` in
   `android/accessibility-companion/app/build.gradle.kts` if the companion changed.
3. Tag the commit and push the tag.
4. Draft the GitHub release against that tag and **publish** it. Publishing — not drafting — is
   what fires `android.yml`, which rebuilds the companion from the tag and attaches the APK to
   the release page as `droidjig-companion-<tag>-debug.apk`.

The attached APK is a **debug** build: signed with Android's universal debug key and flagged
debuggable. That is deliberate for now — a distribution build needs a real keystore in repo
secrets, which does not exist yet — and the asset name says so, so nobody installs it thinking
otherwise.

## Reporting a security issue

Do not open a public issue. See [`SECURITY.md`](SECURITY.md).
