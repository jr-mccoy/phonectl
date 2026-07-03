# Self-Healing Wireless-Debug Reconnection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make phonectl recover automatically when the phone's Wireless-Debugging port changes, by adding a socket port-scan fallback to discovery and wiring it into the pre-action guard.

**Architecture:** A new `AdbBackend.scan_ports()` does a fast, concurrent TCP pre-filter (stdlib sockets) returning open ports; `Connection.rediscover()` gains a scan source (between `probe_ports` and `host_shim`) that verifies each open port via the existing `_try_connect` (`get_state()=="device"`); `Connection.ensure()` falls through to `rediscover()` before raising, so a changed port heals transparently and the scan cost is paid only when actually disconnected.

**Tech Stack:** Python ≥3.9 stdlib only — `socket`, `concurrent.futures.ThreadPoolExecutor`. pytest.

## Global Constraints

- **Stdlib-only runtime** (Python ≥ 3.9) — no new dependencies. `socket` and `concurrent.futures` are stdlib.
- **Backend isolation** — only `src/phonectl/adb_backend.py` performs external device I/O (adb/subprocess/sockets). The connection layer reaches the scan solely through the optional `scan_ports` backend hook (via `getattr`), never sockets directly.
- **TDD, one commit per task** — write the failing test first; one reviewable commit per task.
- **`PHONECTL_HOME` isolation in tests** — every test that reads/writes config sets `monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))`.
- **Behavior-preserving failure path** — when no live device is found, `rediscover()`/`ensure()` still raise `ConnectionError(GUIDANCE)`.
- **Backward compatibility** — the existing `StateBackend` test double lacks `scan_ports`, so the `getattr`-guarded branch is skipped; all five current `tests/test_connection.py` cases must stay green.

---

### Task 1: `AdbBackend.scan_ports()` + injectable probe

**Files:**
- Modify: `src/phonectl/adb_backend.py` (imports; `__init__`; new module fn `_default_port_probe`; new method `scan_ports`)
- Test: `tests/test_adb_backend.py`

**Interfaces:**
- Consumes: nothing (leaf task).
- Produces:
  - `AdbBackend.__init__(self, serial=None, runner=subprocess.run, port_probe=None)` — new optional `port_probe` seam.
  - `AdbBackend.scan_ports(self, ip: str, ports, *, timeout=0.3, workers=200) -> list[int]` — sorted open ports.
  - `_default_port_probe(ip: str, port: int, timeout: float) -> bool` — module-level real socket probe (default).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_adb_backend.py` (the file already has `from phonectl.adb_backend import AdbBackend` at the top):

```python
def test_scan_ports_returns_sorted_open_ports():
    opened = {43091, 8765}
    def fake_probe(ip, port, timeout):
        assert ip == "192.168.0.109"
        return port in opened
    b = AdbBackend(port_probe=fake_probe)
    result = b.scan_ports("192.168.0.109", [50000, 8765, 40000, 43091])
    assert result == [8765, 43091]

def test_scan_ports_empty_when_none_open():
    b = AdbBackend(port_probe=lambda ip, port, timeout: False)
    assert b.scan_ports("192.168.0.109", [1, 2, 3]) == []

def test_scan_ports_empty_input():
    b = AdbBackend(port_probe=lambda ip, port, timeout: True)
    assert b.scan_ports("192.168.0.109", []) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/test_adb_backend.py -k scan_ports -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'port_probe'` / `AttributeError: 'AdbBackend' object has no attribute 'scan_ports'`.

- [ ] **Step 3: Write minimal implementation**

In `src/phonectl/adb_backend.py`, update the imports at the top (currently `import shlex` / `import subprocess`):

```python
import shlex
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor

from phonectl import capabilities, ui_parser


def _default_port_probe(ip: str, port: int, timeout: float) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((ip, port)) == 0
    except OSError:
        return False
    finally:
        s.close()
```

Update `__init__` and add `scan_ports` (place `scan_ports` next to `get_state`/`mdns_services`):

```python
    def __init__(self, serial=None, runner=subprocess.run, port_probe=None):
        self.serial = serial
        self._runner = runner
        self._port_probe = port_probe or _default_port_probe

    def scan_ports(self, ip, ports, *, timeout=0.3, workers=200):
        ports = list(ports)
        if not ports:
            return []
        probe = self._port_probe
        with ThreadPoolExecutor(max_workers=min(workers, len(ports))) as ex:
            pairs = ex.map(lambda p: (p, probe(ip, p, timeout)), ports)
            open_ports = [p for p, is_open in pairs if is_open]
        return sorted(open_ports)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/test_adb_backend.py -k scan_ports -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/adb_backend.py tests/test_adb_backend.py
git commit -m "feat(backend): add scan_ports with injectable socket probe

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: config default + `rediscover()` scan source

**Files:**
- Modify: `src/phonectl/config.py` (add `scan_range` to `DEFAULTS`)
- Modify: `src/phonectl/connection.py:51` (`rediscover` — insert scan branch before the `host_shim` branch)
- Test: `tests/test_connection.py`

**Interfaces:**
- Consumes: `AdbBackend.scan_ports(ip, ports, *, timeout, workers) -> list[int]` (Task 1).
- Produces: `Connection.rediscover()` returns the live `"<ip>:<port>"` discovered by scan and persists it via the existing `connect()`; raises `ConnectionError(GUIDANCE)` if nothing is found.

- [ ] **Step 1: Write the failing tests**

Add a shared fake backend and two tests to `tests/test_connection.py`. Also add `from phonectl import config` to that file's imports (it currently imports `from phonectl.connection import Connection, GUIDANCE`).

```python
class ScanBackend:
    """Fake backend: get_state() reports 'device' only once connected to the target addr."""
    def __init__(self, open_ports, target):
        self.serial = None
        self._open = list(open_ports)
        self._target = target
        self.adb_calls = []
    def get_state(self):
        return "device" if self.serial == self._target else "offline"
    def _adb(self, *args):
        self.adb_calls.append(args)
        return ""
    def scan_ports(self, ip, ports, *, timeout=0.3, workers=200):
        return sorted(self._open)

def test_rediscover_finds_live_port_via_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = ScanBackend(open_ports=[43091], target="192.168.0.109:43091")
    cfg = {"serial": "192.168.0.109:44063", "last_port": "192.168.0.109:44063"}
    addr = Connection(b, cfg).rediscover()
    assert addr == "192.168.0.109:43091"
    assert b.serial == "192.168.0.109:43091"
    assert config.load()["serial"] == "192.168.0.109:43091"

def test_rediscover_skips_open_non_device_ports(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    # 8765 (companion) is open but never 'device'; 43091 is the real adbd port
    b = ScanBackend(open_ports=[8765, 43091], target="192.168.0.109:43091")
    cfg = {"serial": "192.168.0.109:44063", "last_port": "192.168.0.109:44063"}
    addr = Connection(b, cfg).rediscover()
    assert addr == "192.168.0.109:43091"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/test_connection.py -k rediscover -v`
Expected: FAIL — `rediscover()` raises `ConnectionError(GUIDANCE)` because the scan branch does not exist yet (open ports are never tried).

- [ ] **Step 3: Write minimal implementation**

In `src/phonectl/config.py`, add one line to the `DEFAULTS` dict (after `idempotency_ttl`):

```python
    "idempotency_ttl": 300.0,   # how long a finished job stays dedupe-eligible
    "scan_range": [30000, 50000],  # [start, end] port band for reconnect self-heal scan
```

In `src/phonectl/connection.py`, `rediscover()` currently runs the `probe_ports` loop, then the `host_shim` branch. Insert the scan branch between them — immediately after the `probe_ports` `for` loop and before `shim = getattr(...)`:

```python
        scan = getattr(self.backend, "scan_ports", None)
        if scan is not None:
            start, end = self.cfg.get("scan_range", [30000, 50000])
            tried = {self.cfg.get("last_port"), self.cfg.get("serial")}
            for port in scan(ip, range(start, end + 1)):
                addr = f"{ip}:{port}"
                if addr in tried:
                    continue
                if self._try_connect(addr):
                    return addr
```

(`ip` is already defined above via `ip = self._device_ip()`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/test_connection.py -k rediscover -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/config.py src/phonectl/connection.py tests/test_connection.py
git commit -m "feat(connection): scan fallback in rediscover for changed wireless port

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `ensure()` auto-recover before raising

**Files:**
- Modify: `src/phonectl/connection.py:26` (`ensure` — replace final `raise` with `rediscover()` fall-through)
- Test: `tests/test_connection.py`

**Interfaces:**
- Consumes: `Connection.rediscover()` (Task 2) and `ScanBackend` (defined in `tests/test_connection.py` in Task 2).
- Produces: `Connection.ensure()` returns normally when the scan recovers a live device; still raises `ConnectionError(GUIDANCE)` when nothing is found.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_connection.py` (reuses `ScanBackend` and `config` added in Task 2):

```python
def test_ensure_auto_recovers_via_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = ScanBackend(open_ports=[43091], target="192.168.0.109:43091")
    cfg = {"serial": "192.168.0.109:44063", "last_port": "192.168.0.109:44063"}
    Connection(b, cfg).ensure()  # must not raise
    assert b.serial == "192.168.0.109:43091"
    assert config.load()["serial"] == "192.168.0.109:43091"

def test_ensure_raises_guidance_when_scan_finds_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PHONECTL_HOME", str(tmp_path))
    b = ScanBackend(open_ports=[], target="192.168.0.109:43091")
    cfg = {"serial": "192.168.0.109:44063", "last_port": "192.168.0.109:44063"}
    with pytest.raises(ConnectionError) as e:
        Connection(b, cfg).ensure()
    assert GUIDANCE in str(e.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/test_connection.py -k auto_recovers -v`
Expected: FAIL — `ensure()` raises `ConnectionError(GUIDANCE)` today (it never reaches `rediscover()`), so `test_ensure_auto_recovers_via_scan` fails. (`test_ensure_raises_guidance_when_scan_finds_nothing` may already pass — that is fine.)

- [ ] **Step 3: Write minimal implementation**

In `src/phonectl/connection.py`, `ensure()` currently ends:

```python
        serial = self.cfg.get("serial") or self.cfg.get("last_port")
        if serial:
            self.connect(serial)
            if self.backend.get_state() == "device":
                return
        raise ConnectionError(GUIDANCE)
```

Replace the final `raise ConnectionError(GUIDANCE)` line with a fall-through to `rediscover()` (which connects-and-returns on success, or raises the same `GUIDANCE`):

```python
        serial = self.cfg.get("serial") or self.cfg.get("last_port")
        if serial:
            self.connect(serial)
            if self.backend.get_state() == "device":
                return
        self.rediscover()  # mdns/probe/scan fallback; raises GUIDANCE if no live device
```

- [ ] **Step 4: Run the full connection suite to verify pass + no regressions**

Run: `PYTHONPATH=src pytest tests/test_connection.py -v`
Expected: PASS — the two new tests plus all five pre-existing `test_ensure_*` cases (the `StateBackend` double has no `scan_ports`, so its scan branch is skipped and old behavior is preserved).

- [ ] **Step 5: Commit**

```bash
git add src/phonectl/connection.py tests/test_connection.py
git commit -m "feat(connection): auto-recover in ensure() via rediscover scan

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the entire test suite**

Run: `PYTHONPATH=src pytest -q`
Expected: all tests pass (prior baseline 628 passed / 1 skipped, now +7 new tests). No failures, no errors.

- [ ] **Step 2: If green, no commit needed.** If any pre-existing test regressed, stop and diagnose with superpowers:systematic-debugging before proceeding.

---

## Self-Review

**Spec coverage:**
- §4.1 `scan_ports` + `port_probe` seam + `_default_port_probe` → Task 1. ✓
- §4.2 `rediscover()` scan source (order, `_device_ip`, `scan_range`, `_try_connect` verification, skip-already-tried) → Task 2. ✓
- §4.3 `ensure()` auto-recover → Task 3. ✓
- §4.4 config `scan_range` default → Task 2 Step 3. ✓
- §5 behavior-preserving failure path → Task 3 (`test_ensure_raises_guidance_when_scan_finds_nothing`) + Global Constraints. ✓
- §6 testing (scan_ports, rediscover branch incl. non-adb skip, ensure both paths, regression) → Tasks 1–4. ✓
- §7 backend isolation → scan lives in `adb_backend.py`; connection uses `getattr` hook. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; exact commands with expected output. ✓

**Type consistency:** `scan_ports(ip, ports, *, timeout, workers) -> list[int]` and `port_probe(ip, port, timeout) -> bool` are identical across Task 1 (definition), Task 2 (consumer), and the `ScanBackend` fake. `_try_connect(addr) -> bool` and `connect(addr)` match `connection.py`. `scan_range` key matches between `config.py` and `rediscover()`. ✓
