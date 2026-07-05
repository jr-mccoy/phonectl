import time

from phonectl import config

GUIDANCE = (
    "Cannot reach the device. Enable Settings > Developer options > "
    "Wireless debugging, then run: phonectl setup"
)

class Connection:
    # Freshness window during which ensure() trusts the last successful check
    # instead of spawning `adb get-state` again. Bounded staleness: a link that
    # dies inside the window surfaces as the next command's failure, and the
    # first ensure() after expiry re-checks and self-heals as before.
    ENSURE_TTL = 5.0

    def __init__(self, backend, cfg: dict):
        self.backend = backend
        self.cfg = cfg
        self._ensured_at = None

    def pair(self, addr: str, code: str) -> None:
        self.backend._adb("pair", addr, code)

    def connect(self, addr: str, *, persist: bool = True) -> None:
        self.backend._adb("connect", addr)
        self.backend.serial = addr
        if persist:
            self.cfg["serial"] = addr
            self.cfg["last_port"] = addr
            config.save(self.cfg)

    def ensure(self, monotonic=time.monotonic) -> None:
        ttl = self.cfg.get("ensure_ttl", self.ENSURE_TTL) or 0
        if (ttl > 0 and self._ensured_at is not None
                and monotonic() - self._ensured_at < ttl):
            return
        self._ensure_now()
        self._ensured_at = monotonic()   # only stamped when _ensure_now succeeded

    def _ensure_now(self) -> None:
        try:
            self._ensure_adb()
        except (ConnectionError, FileNotFoundError, OSError):
            # ADB recovery exhausted. A live companion still serves the whole
            # observe→act loop natively (tree, keyguard/focus, gestures,
            # screenshots), so a dead ADB link degrades instead of failing —
            # ADB-only helpers surface their own errors if actually needed.
            if self._companion_serves_observe():
                return
            raise

    def _companion_serves_observe(self) -> bool:
        fn = getattr(self.backend, "for_capability", None)
        if fn is None:
            return False   # bare AdbBackend — no other provider to degrade to
        try:
            return fn("observe_ui_native") is not None
        except Exception:
            return False

    def _ensure_adb(self) -> None:
        if self.backend.get_state() == "device":
            return
        wake = getattr(self.backend, "wake", None)
        if wake is not None:
            wake()
            if self.backend.get_state() == "device":
                return
        serial = self.cfg.get("serial") or self.cfg.get("last_port")
        if serial:
            self.connect(serial)
            if self.backend.get_state() == "device":
                return
        self.rediscover()  # mdns/probe/scan fallback; raises GUIDANCE if no live device

    def _try_connect(self, addr: str) -> bool:
        self.connect(addr)
        return self.backend.get_state() == "device"

    def _device_ip(self) -> str:
        addr = self.cfg.get("last_port") or self.cfg.get("serial") or ""
        if ":" in addr:
            return addr.rsplit(":", 1)[0]
        return "127.0.0.1"

    def rediscover(self, sleep=time.sleep) -> str:
        for addr in (self.cfg.get("last_port"), self.cfg.get("serial")):
            if addr and self._try_connect(addr):
                return addr
        mdns = getattr(self.backend, "mdns_services", None)
        if mdns is not None:
            for addr in mdns():
                if self._try_connect(addr):
                    return addr
        ip = self._device_ip()
        ports = self.cfg.get("probe_ports", [])
        for n, port in enumerate(ports):
            addr = f"{ip}:{port}"
            if self._try_connect(addr):
                return addr
            if n < len(ports) - 1:
                sleep(0)
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
        shim = getattr(self.backend, "host_shim_runner", None)
        if shim is not None:
            alt = type(self.backend)(serial=self.backend.serial, runner=shim())
            for addr in (self.cfg.get("last_port"), self.cfg.get("serial")):
                if addr:
                    alt._adb("connect", addr)
                    if alt.get_state() == "device":
                        self.backend = alt
                        self.cfg["serial"] = self.cfg["last_port"] = addr
                        config.save(self.cfg)
                        return addr
        raise ConnectionError(GUIDANCE)
