# Installing and connecting droidjig

Getting from nothing to a connected phone. If you want the guided version, `droidjig setup`
walks the same path interactively — see [setup-walkthrough.md](setup-walkthrough.md) for its
prompt-by-prompt behavior.

---

## Install

### 1. Install adb

**In Termux (host):**
```bash
pkg install android-tools
```

**In a PRoot-Distro Debian/Ubuntu distro:**
```bash
apt-get update && apt-get install -y android-tools-adb
```

### 2. Install droidjig

```bash
# From the repo root (requires setuptools):
pip install -e .
```

---

## Pair and connect (Android 11+ Wireless Debugging)

This is a one-time pairing step. On the phone:

**Settings → Developer options → Wireless debugging → "Pair device with pairing code"**

Note the `IP:PORT` shown for pairing and the 6-digit code, then run:

```bash
# Step 1: pair (use the pairing port and code from the Wireless Debugging screen)
adb pair 127.0.0.1:<pairPort> <code>

# Step 2: connect (use the main Wireless Debugging port, not the pairing port)
adb connect 127.0.0.1:<connPort>

# Step 3: verify
droidjig doctor
# Expected: droidjig: connected (serial=127.0.0.1:<connPort>, state=device)
```

If `droidjig doctor` prints a guidance message instead of "connected", see the topology fallback in [integration-smoke.md](integration-smoke.md).

---

## Getting started: `droidjig setup`

`droidjig setup` is the recommended onboarding wizard. It detects whether `adb` is installed, guides Android 11+ Wireless Debugging pairing, connects to the device, verifies `adb get-state`, and persists the working serial plus the volatile Wireless Debugging connect port for later reconnect attempts.

If `adb` is missing in Termux, install it first:

```bash
pkg install android-tools
```

Run the wizard and answer the three prompts from the Wireless Debugging screen:

```bash
droidjig setup
# Pairing host:port: 127.0.0.1:<pairPort>
# 6-digit pairing code: <code>
# Connect host:port: 127.0.0.1:<connPort>
```

Re-running `droidjig setup` is idempotent: if the device is already connected, droidjig short-circuits with an "already connected" message and does not prompt again. Setup can also report provider modules:

```bash
droidjig setup adb
droidjig setup accessibility
droidjig setup notifications
droidjig setup termux-api
droidjig setup all
```

Each module report states the required permission, current availability, how to enable it, capabilities unlocked, and safety implications. `accessibility` and `notifications` are served by the companion APK; `termux-api` is optional and discovered from the local Termux:API commands.

## Diagnostics

`droidjig doctor` checks connectivity; `droidjig doctor --json` returns the structured result envelope with connection state and backend capabilities.

```bash
droidjig doctor
droidjig doctor --json
```

For support, write a redacted diagnostics bundle:

```bash
droidjig doctor --bundle /tmp/droidjig-diag.zip
```

The bundle contains `manifest.json`, `adb-version.txt`, and `adb-devices.txt`. The manifest includes config with secrets masked, capability status, device state, `adb version`, `adb devices -l`, mDNS results when available, host-shim status, and a metadata-only audit tail (`ts`/`verb`/`app`/`hash`).

---
