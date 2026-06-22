import time

from phonectl import config

GUIDANCE = (
    "Cannot reach the device. Enable Settings > Developer options > "
    "Wireless debugging, then run: phonectl setup"
)

class Connection:
    def __init__(self, backend, cfg: dict):
        self.backend = backend
        self.cfg = cfg

    def pair(self, addr: str, code: str) -> None:
        self.backend._adb("pair", addr, code)

    def connect(self, addr: str, *, persist: bool = True) -> None:
        self.backend._adb("connect", addr)
        self.backend.serial = addr
        if persist:
            self.cfg["serial"] = addr
            self.cfg["last_port"] = addr
            config.save(self.cfg)

    def ensure(self) -> None:
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
        raise ConnectionError(GUIDANCE)

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
