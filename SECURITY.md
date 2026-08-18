# Security Policy

droidjig hands an AI agent real control of a real phone. That is the whole point of the project,
and it is also why the threat model is written down rather than assumed.

## Supported versions

droidjig is pre-1.0. Only the tip of `master` is supported; fixes land there and are not
backported.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's private vulnerability reporting on this repository:
[**Report a vulnerability**](https://github.com/jr-mccoy/droidjig/security/advisories/new).
That opens a private advisory visible only to the maintainer.

A useful report includes:

- what an attacker gets (device control? audit-log forgery? kill-switch bypass? data read?),
- who the attacker is in the threat model below — another app on the device is in scope, a
  remote network attacker generally is not,
- a reproduction: the commands, the state files involved, and what you observed.

Expect a first response within a week. Because this is a personal project with one maintainer,
please do not treat that as an SLA.

## Threat model

The model this project actually designs against:

- **In scope: other apps on the same device.** On Android, loopback is *not* a UID boundary —
  any local app holding `INTERNET` can connect to a listening socket on `127.0.0.1`. Both local
  control surfaces (the companion socket and the daemon's JSON-RPC port) are therefore
  authenticated with a per-run 128-bit shared secret compared in constant time, and the secret
  lives in app-private storage.
- **In scope: the agent itself.** The agent driving droidjig is treated as untrusted with
  respect to its own safety gates. It cannot clear the emergency stop, and every action it takes
  funnels through `runtime.run_action` for the mode gate, kill switch, risk classification,
  policy, rate limit, idempotency, and audit log.
- **Out of scope: device root, a malicious ADB host, and a compromised OS.** droidjig runs
  unrooted and inherits whatever the platform provides. Anything with root already outranks
  every control here.
- **Out of scope: remote attackers.** Nothing listens on a non-loopback interface.

## Known residual risk

The project ships its own adversarial security review —
[`docs/adversarial-review-2026-07.md`](docs/adversarial-review-2026-07.md) — with 16 findings,
`file:line` evidence, and their remediation. All 16 are fixed. What remains open is stated there
and repeated here so it is not buried:

- **The Kotlin companion's fixes are unit-tested, not instrumented-tested.** CI compiles the APK
  and runs JVM unit tests; it runs no emulator, so `connectedAndroidTest` and the manual
  on-device matrix in [`docs/integration-smoke.md`](docs/integration-smoke.md) are the only
  proof of runtime behavior.
- **There is no human-only sensitive-action policy layer yet.** Until there is, the honest
  posture is that droidjig is built for **supervised** agent use, not unattended autonomy.

The default action mode is `confirm` for this reason. Read
[Safety](docs/safety.md) before switching to `auto`, and read the review before any unattended
use.

## Hardening notes for users

- Keep `mode: confirm` unless you are watching the session.
- The kill switch works without the companion (a `STOP` sentinel file in `$DROIDJIG_HOME`), so
  it stays available even if the APK is not running.
- `actions.jsonl` is an append-only audit log of every action taken. Read it after an agent run;
  that is what it is for.
- Guarded apps (banking, authenticator, and similar) can be listed in config so that actions,
  observation, screenshots, and OCR against them are all refused.
