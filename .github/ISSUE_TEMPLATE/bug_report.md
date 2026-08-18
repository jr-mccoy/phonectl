---
name: Bug report
about: Something droidjig did wrong
labels: bug
---

<!-- Security problems do not belong here. See SECURITY.md for private reporting. -->

## What happened

<!-- What you ran, what you expected, what you got. Paste the `--json` envelope if there is one:
     it carries the error code, the provider that served the call, and the user_action. -->

```console
$ droidjig ...
```

## `droidjig doctor`

<!-- Paste the output. It reports the connection, the config, the providers, and the companion
     state, which is most of what a diagnosis needs. -->

```console
$ droidjig doctor
```

## Environment

- droidjig version / commit:
- Python version:
- Host: <!-- Termux + PRoot-Distro? plain Linux? macOS? -->
- Phone and Android version: <!-- e.g. Pixel 7, Android 14 -->
- Companion APK installed: <!-- yes / no -->
- Daemon running: <!-- yes / no -->

## Anything else

<!-- Relevant lines from actions.jsonl are often the fastest path to a diagnosis. Scrub anything
     private first — the audit log records what you did on your phone. -->
