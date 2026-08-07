# Pushed-token v2 (trust-on-first-use pairing) — design note

**Status:** code-complete, **not yet device-validated**; a security review is owed before this
is enabled for release builds.

## Problem

Today the companion mints its own pairing token on first read of
`SharedPrefsTrustState.companionToken()` and phonectl obtains it one of two ways:

- **B (debug builds):** `adb shell run-as com.phonectl.companion cat …` reads the SharedPrefs value.
- **C (fallback):** the token is shown in the Settings UI and the user pastes it into
  `phonectl config` (`companion_token`).

`run-as` only works on **debuggable** builds; a release build leaves only manual paste. v2 lets
phonectl **mint** the token and **push** it to the companion at first pair, so release builds need
neither `run-as` nor manual paste.

## Approach A — phonectl-minted token, pushed via a first-pair broadcast

phonectl generates a random token and sends it to the companion:

```
adb shell am broadcast -n com.phonectl.companion/.service.LifecycleReceiver \
    -a com.phonectl.companion.action.SET_TOKEN --es token <minted-token>
```

The companion adopts it **trust-on-first-use**: only when no token is set yet. Once a token exists,
`SET_TOKEN` is ignored — an unauthenticated broadcast can never overwrite an established secret.

### Invariant (load-bearing)

**Once a token exists, never accept an unauthenticated set.** The wrinkle is that
`companionToken()` *generates on read*, so any prior read (e.g. the Settings UI displaying the
token) creates a token and closes the TOFU window. The receiver therefore checks a
**non-generating** `hasToken()`, and adoption is decided by the pure
`LifecycleAuth.authorizedFirstPair(supplied, hasExistingToken)`:

| supplied | hasExistingToken | adopt? |
|---|---|---|
| non-blank | false | **yes** |
| non-blank | true  | no (never overwrite) |
| blank/null | any  | no |

### Threat model / caveats (why a security review is owed)

- **TOFU race:** between install and the push, a malicious local app could send its own `SET_TOKEN`
  first (loopback is not a UID boundary on Android — Finding 2). Whoever sets first wins. On a
  personal device the setup flow pushes during `phonectl companion setup`, immediately after
  install, minimizing the window — but the window is non-zero. Document this; do not enable
  pushed-token as the default for release builds until reviewed.
- **Ordering:** if the user opens the Settings UI before the push, the UI's `companionToken()` read
  mints a token and the push is (correctly) ignored; phonectl then falls back to B/C. The push path
  is therefore best-effort and additive, never a regression.
- START/STOP lifecycle broadcasts continue to require the **already-paired** token
  (`LifecycleAuth.authorized`); only `SET_TOKEN` uses the first-pair rule.

## Surface

- Kotlin: `SharedPrefsTrustState.hasToken()` / `setToken()`, `LifecycleAuth.authorizedFirstPair`,
  `LifecycleReceiver` `SET_TOKEN` handling, manifest intent-filter action. JVM tests cover the pure
  auth truth table.
- Python: `companion_setup.push_token(adb, token)` mints+broadcasts; opt-in, additive.

## Not done

On-device validation of the broadcast pairing; the security review of the TOFU race before making
pushed-token the default for release builds.
