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

    def connect(self, addr: str) -> None:
        self.backend._adb("connect", addr)
        self.backend.serial = addr
        self.cfg["serial"] = addr
        config.save(self.cfg)

    def ensure(self) -> None:
        if self.backend.get_state() == "device":
            return
        serial = self.cfg.get("serial")
        if serial:
            self.connect(serial)
            if self.backend.get_state() == "device":
                return
        raise ConnectionError(GUIDANCE)
