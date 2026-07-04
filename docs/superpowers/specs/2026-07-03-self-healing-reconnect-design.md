# phonectl — self-healing wireless-debug reconnection

**Date:** 2026-07-03
**Status:** Design spec. Required before its implementation plan.
**Author:** Jeremy McCoy (with Claude)

**Reads with:**

- `src/phonectl/connection.py` — the `Connection` class this spec extends (`ensure`, `rediscover`).
- `src/phonectl/adb_backend.py` — the backend that owns all external device I/O; the new port scan lives here
  to preserve the backend-isolation invariant.
- Memory `phonectl-adb-reconnect` — the operational incident (2026-07-03) that motivated this: the port shown
  on the phone's Wireless Debugging screen was **not** the live adbd connect port (displayed `:44063`, actual
  `:43091`), mDNS returned nothing in Termux, and phonectl's stored serial was stale — stranding reconnection.

This is a **design document** — goals, the problem, locked decisions, interfaces, testing. No TDD tasks;
those live in the plan.

---

## 1. Problem

Android assigns a **new random port** to Wireless Debugging every time it is toggled, reboots, or the Wi-Fi
reconnects. phonectl stores the last-known `serial`/`last_port` in config and, when that goes stale, cannot
recover on this host:

- `Connection.ensure()` (the pre-action guard) tries only the **stored serial** + `wake()`, then raises
  `ConnectionError(GUIDANCE)`. A changed port strands every action until the user manually fixes config.
- `Connection.rediscover()` (behind `./pc reconnect`) tries `last_port → serial → mdns_services → probe_ports
  → host_shim`. In this Termux+PRoot environment **mDNS returns nothing** (multicast restricted) and
  `probe_ports` has **no default** (`config.DEFAULTS` omits the key, so it is always `[]`). Both discovery
  sources are dead, so `rediscover()` gives up.

The manual recovery required a loopback port scan to find the live adbd port, then a hand-edit of the config
serial. This is counter to the autonomy/resilience north-star: a changed port should heal transparently.

## 2. Goals & non-goals

**Goals:**
- A stale/changed wireless-debug port heals **automatically** before an action fails — `ensure()` recovers
  when the device is genuinely reachable on a new port.
- `./pc reconnect` finds the live port with **no arguments**, even when mDNS yields nothing.
- Discovery is bounded, fast (~seconds), deterministic, and unit-testable with **no real device and no real
  sockets** (injected probe, stdlib-only).
- The backend-isolation invariant holds: the only new external I/O (a TCP port scan) lives in
  `adb_backend.py`.

**Non-goals:**
- No change to pairing (`adb pair`) — pairing keys already persist; this is purely about the *connect* port.
- No mDNS fix (environment limitation, out of scope).
- No new user-facing CLI surface — `./pc reconnect` and the pre-action guard are the existing entry points.

## 3. Locked decisions

1. **Socket-scan fallback, not a curated port list.** The live port is random across a wide range; a fixed
   `probe_ports` list would rarely contain it, and `rediscover()` does an `adb connect`+`get_state` per probe
   (seconds each) — too slow to brute-force. A fast socket pre-filter that returns only *open* ports, then
   verifies each with the existing `_try_connect`, is the only viable shape. (Explicit `probe_ports` from
   config is retained as a fast-path that runs before the scan.)
2. **Auto-recover in `ensure()`, not just `./pc reconnect`.** The scan cost is paid **only** when the device
   is actually unreachable — normal actions still hit the stored serial instantly. Aligns with the autonomy
   north-star.
3. **Scan lives in `adb_backend.py`.** Backend isolation: the one module permitted external device I/O.
4. **Verification gates safety.** The scan only proposes candidate open ports; each is accepted only if
   `get_state() == "device"` after connect. Non-adb open ports (e.g. the companion APK on `8765`, the local
   adb server on `5037`) are auto-rejected — no port blocklist needed.

## 4. Interfaces

### 4.1 `AdbBackend` (adb_backend.py)

```python
def __init__(self, serial=None, runner=subprocess.run, port_probe=None):
    ...
    self._port_probe = port_probe or _default_port_probe   # injectable seam (mirrors `runner`)

def scan_ports(self, ip, ports, *, timeout=0.1, workers=200) -> list[int]:
    """Return the sorted list of ports open on `ip`, probed concurrently."""
```

- `_default_port_probe(ip, port, timeout) -> bool` uses `socket.connect_ex` (stdlib). Returns `True` on open.
  It is also the **sentinel** for the default path: `scan_ports` branches on `self._port_probe is
  _default_port_probe`.
- **Default path — `selectors` (non-blocking), not threads.** On-device measurement (§6.1) showed a
  thread-per-port blocking scan is unusable over the phone's Wi-Fi interface: closed ports are *filtered*
  (silently dropped) rather than RST, so each blocking connect burns the full timeout, and 512–1500 worker
  threads add catastrophic GIL/scheduling overhead (74–546s for the ephemeral range). The default path instead
  uses `_scan_via_selectors(ip, ports, timeout)`: it fires a batch of non-blocking `connect_ex` calls
  (batch size = FD budget − 64, capped 2000; soft `RLIMIT_NOFILE` raised toward hard best-effort) and waits a
  single `selectors` window per batch, so filtered ports wait *in parallel*. Wall-clock ≈ num_batches ×
  timeout, independent of worker count — **~10s for the full 32768–61000 band at timeout 0.1**.
- **Injected-probe path — threads (tests only).** When a custom `port_probe` is supplied, `scan_ports` fans it
  over `ports` via `ThreadPoolExecutor(workers)` — simple, one call per port. Tests inject a probe that
  consults an in-memory open-set (no sockets, no device); a separate test exercises the real `selectors` path
  against a live loopback listener.

### 4.2 `Connection.rediscover()` (connection.py)

Insert a scan source between `probe_ports` and `host_shim`, guarded by `getattr` (optional backend hook, like
`mdns_services`/`host_shim_runner`):

```
last_port → serial → mdns_services → probe_ports → scan_ports(ip, scan_range) → host_shim
```

- `ip` from the existing `_device_ip()` helper.
- `scan_range` from `cfg["scan_range"]` (default `[32768, 61000]` — the full Linux/Android ephemeral band adbd
  binds within; a narrower band risks missing a port that landed high, e.g. 53601/60948 observed on-device);
  the scanned iterable is `range(start, end + 1)`.
- Each open port `p` → `_try_connect(f"{ip}:{p}")` (existing method: connects, persists config, returns
  `get_state() == "device"`). First success returns its addr. Ports already tried (`last_port`/`serial`) may
  be skipped to avoid redundant connects.

### 4.3 `Connection.ensure()` (connection.py)

Before the final `raise ConnectionError(GUIDANCE)`, fall through to `self.rediscover()` (which either connects
and returns, or raises the same `GUIDANCE`). Normal/offline-then-device paths are unchanged — `rediscover()`
is only reached when the stored-serial reconnect has already failed.

### 4.4 Config (config.py)

Add to `DEFAULTS`: `"scan_range": [32768, 61000]`. `timeout`/`workers` remain method defaults (not config
surface — YAGNI).

## 5. Error handling

The failure path is behavior-preserving: when no scanned port yields `get_state() == "device"`, `rediscover()`
and `ensure()` raise the same `ConnectionError(GUIDANCE)` as today.

## 6. Testing

Stdlib-only, injected seams, no real device or sockets. TDD, one commit per task.

1. **`scan_ports`** — inject a `port_probe` reporting a fixed open-set; assert returns the sorted open ports;
   empty open-set → `[]`.
2. **`rediscover` scan branch** — fake backend exposes `scan_ports → [43091]` and a `get_state` that returns
   `"device"` only once connected to the target addr; assert `rediscover()` returns `"<ip>:43091"` and
   persists it to config. Also: scan returns a non-adb port first (stays `"offline"`) → skipped, correct port
   still found.
3. **`ensure` auto-recover** — stored serial dead + scan finds the device → `ensure()` returns without raising
   and config is updated; scan finds nothing → raises `GUIDANCE`.
4. **Regression** — all five existing `tests/test_connection.py` cases stay green (the `StateBackend` double
   lacks `scan_ports`, so the `getattr`-guarded branch is skipped — old behavior preserved).
5. **Real selectors path** — a test scans a live loopback listener (default probe, no injection) and asserts
   the listening port is found and a just-released port is not.

### 6.1 On-device verification (2026-07-03, Galaxy S25 Ultra)

Validated end-to-end against the physical device — the repo rule is "don't claim device behavior you haven't
run on-device," and this pass materially changed the design:

- **`./pc reconnect`** with a poisoned (dead) config serial scanned, found the live adbd port, connected, and
  persisted it in **~8s**, no manual input.
- **`./pc observe`** auto-recovered through `ensure()` → `rediscover()` → scan in **~18s**, then reported
  device state (screen locked → correctly refused to act — safe-by-default, connection healed).
- **Scan-strategy finding (drove §4.1):** the initial `ThreadPoolExecutor` design took **54.8s** for
  30000–50000 and hung `observe`; high-thread variants were far worse (74–546s). The `selectors` rewrite does
  the full 32768–61000 band in **~10.5s** at timeout 0.1. Live adbd ports observed spanned 41491–53601,
  confirming the range must cover the whole ephemeral band.

## 7. Backend-isolation note

The socket scan is the only new external I/O and lives in `adb_backend.py`. `connection.py` reaches it solely
through the optional `scan_ports` backend hook — no direct sockets/`subprocess` in the connection layer.
