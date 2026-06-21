# Integration smoke test — real-device procedure

This document describes the full manual end-to-end test for `phonectl`. It requires a **physical Android 11+ phone with Wireless Debugging enabled** and is **not automated** — it cannot run in CI because it depends on a paired device. All steps below are pending the user's physical device.

---

## Prerequisites

- An Android phone running Android 11 or later.
- Developer options enabled on the phone (Settings → About phone → tap "Build number" 7 times).
- Wireless Debugging enabled: Settings → Developer options → Wireless debugging → toggle ON.
- The phone and the computer/distro running `phonectl` must share the loopback (`127.0.0.1` is sufficient when running inside PRoot-Distro on the same device).

---

## Step 1: Install adb and phonectl

**Inside a PRoot-Distro Debian/Ubuntu distro:**
```bash
apt-get update && apt-get install -y android-tools-adb
```

**Or in host Termux:**
```bash
pkg install android-tools
```

**Then install phonectl** from the repo root:
```bash
pip install -e .
```

Verify both are available:
```bash
adb version
phonectl --version
```

---

## Step 2: Pair and connect (build-step-zero connectivity proof)

This step validates the core topology assumption: that an `adb` client running inside PRoot-Distro (or host Termux) can reach `adbd` on the device over `127.0.0.1`.

### 2a. Pair the device

On the phone: **Settings → Developer options → Wireless debugging → "Pair device with pairing code"**

The phone shows a pairing IP:PORT and a 6-digit code. Run:

```bash
adb pair 127.0.0.1:<pairPort> <code>
```

Expected output: `Successfully paired to 127.0.0.1:<pairPort> [guid=...]`

### 2b. Connect to the device

After pairing, return to the main Wireless Debugging screen. It shows the main connection port (a different port from the pairing port). Run:

```bash
adb connect 127.0.0.1:<connPort>
```

Expected output: `connected to 127.0.0.1:<connPort>`

### 2c. Verify with phonectl doctor

```bash
phonectl doctor
```

Expected output:
```
phonectl: connected (serial=127.0.0.1:<connPort>, state=device)
```

If this prints the guidance string ("Cannot reach the device...") instead, see the **Host-Termux fallback** section below.

---

## Step 3: Run the observe-act smoke scenario

This scenario exercises the full observe → act → observe loop using the Settings app.

### 3a. Launch the Settings app

```bash
phonectl launch com.android.settings
```

Expected: JSON snapshot with `"app": {"package": "com.android.settings", ...}`.

### 3b. Wait for a known element to appear

```bash
phonectl wait-for --text "Network & internet" --timeout 8
```

Expected: exits with code 0 and prints a JSON snapshot containing an element whose `text` is `"Network & internet"`. If it times out (exit code 1), Settings may be on a different screen — run `phonectl observe` to check the current state.

### 3c. Observe and count elements

```bash
phonectl observe | python -c "import sys, json; d = json.load(sys.stdin); print(len(d['elements']), 'elements,', d['app'])"
```

Expected: a count of visible elements and the current foreground app.

Note the `app` field and the `hash` field from the pre-tap snapshot — they will change after tapping.

### 3d. Tap an element

Identify the index `i` of the "Network & internet" entry from the observe output (look for `"text": "Network & internet"` and its `"i"` value), then:

```bash
phonectl tap --index <i>
```

Expected: JSON snapshot of the post-tap screen. The `hash` field should differ from the pre-tap snapshot, and `app` or the list of elements should reflect the navigation to the Wi-Fi / network settings screen.

### 3e. Confirm the action landed

Run observe again and compare the `hash` to the value from step 3c:

```bash
phonectl observe
```

A changed `hash` (and a changed element list) confirms the tap was delivered and the screen transitioned. This is the success criterion for the smoke run.

---

## Host-Termux fallback

If `phonectl doctor` in step 2c prints:

```
Cannot reach the device. Enable Settings > Developer options > Wireless debugging, then run: phonectl setup
```

the most likely cause is that `adb` inside the PRoot distro cannot reach `adbd` over the loopback — a known risk when PRoot applies ptrace or socket restrictions that block the adb connection (see spec §13, open risks).

**Resolution: run phonectl from host Termux instead.**

1. Exit the PRoot distro and open a host Termux session.
2. Install adb and phonectl in host Termux:
   ```bash
   pkg install android-tools
   pip install -e /path/to/phonectl-repo
   ```
3. Repeat steps 2a–2c from host Termux. Host Termux has direct access to the device loopback without PRoot mediation.
4. Once `phonectl doctor` reports "connected" from host Termux, run all subsequent phonectl commands there.

The interface (CLI, config, audit log) is identical from both environments — only the location of the `adb` binary changes. The topology fallback is described in the design spec (§4.1): host Termux's `adb` reaches `adbd` through the same loopback, just without PRoot in between.

---

## Pending items (requires physical device)

The following steps are **not yet completed** and are deferred until a physical Android 11+ device is available:

- [ ] Confirm `adb pair` / `adb connect` succeeds from inside PRoot-Distro (build-step-zero).
- [ ] Confirm `phonectl doctor` reports "connected" from PRoot-Distro, or fall back to host Termux and confirm it there.
- [ ] Run the observe → launch → wait-for → observe → tap → observe smoke scenario end-to-end and verify `hash` changes between pre- and post-tap snapshots.
- [ ] Record the actual `serial`, port numbers, and any ROM-specific quirks encountered.
- [ ] If the host-Termux fallback is needed, document whether the PRoot adb block is consistent or intermittent.
