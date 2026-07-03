import shlex
import selectors
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from phonectl import capabilities, ui_parser

try:
    import resource
except ImportError:  # pragma: no cover - non-unix platforms
    resource = None


def _default_port_probe(ip: str, port: int, timeout: float) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((ip, port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def _fd_budget(default: int = 256) -> int:
    """Best-effort available file descriptors (raises soft limit toward hard)."""
    if resource is None:
        return default
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft < hard:
            resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
            soft = hard
        return soft
    except (ValueError, OSError):
        return default


def _scan_via_selectors(ip, ports, timeout):
    """Non-blocking concurrent connect scan. Fires a batch of connects at once and
    waits a single timeout window, so filtered (dropped) ports wait in parallel,
    not in series. Wall-clock ~= (num_batches * timeout), independent of workers."""
    batch = max(64, min(_fd_budget() - 64, 2000))
    open_ports = []
    for i in range(0, len(ports), batch):
        sel = selectors.DefaultSelector()
        pending = {}
        for p in ports[i:i + batch]:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setblocking(False)
            if s.connect_ex((ip, p)) == 0:
                open_ports.append(p)  # immediate connect (rare)
                s.close()
                continue
            try:
                sel.register(s, selectors.EVENT_WRITE, p)
                pending[s] = p
            except (KeyError, ValueError, OSError):
                s.close()
        end = time.monotonic() + timeout
        while pending:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            events = sel.select(remaining)
            if not events:
                break
            for key, _ in events:
                s = key.fileobj
                if s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0:
                    open_ports.append(key.data)
                sel.unregister(s)
                s.close()
                del pending[s]
        for s in pending:
            sel.unregister(s)
            s.close()
        sel.close()
    return sorted(open_ports)


class AdbBackend:
    def __init__(self, serial=None, runner=subprocess.run, port_probe=None):
        self.serial = serial
        self._runner = runner
        self._port_probe = port_probe or _default_port_probe

    def scan_ports(self, ip, ports, *, timeout=0.1, workers=200):
        ports = list(ports)
        if not ports:
            return []
        if self._port_probe is not _default_port_probe:
            # Injected probe (tests): simple thread fan-out, one call per port.
            probe = self._port_probe
            with ThreadPoolExecutor(max_workers=min(workers, len(ports))) as ex:
                pairs = ex.map(lambda p: (p, probe(ip, p, timeout)), ports)
                return sorted(p for p, is_open in pairs if is_open)
        return _scan_via_selectors(ip, ports, timeout)

    def _base(self) -> list[str]:
        return ["adb", "-s", self.serial] if self.serial else ["adb"]

    def _adb(self, *args: str) -> str:
        cmd = self._base() + list(args)
        res = self._runner(cmd, capture_output=True, text=True)
        return res.stdout

    def run_adb(self, *args: str):
        cmd = self._base() + list(args)
        return self._runner(cmd, capture_output=True, text=True)

    def _adb_bytes(self, *args: str) -> bytes:
        cmd = self._base() + list(args)
        res = self._runner(cmd, capture_output=True)
        return res._bytes if hasattr(res, "_bytes") else res.stdout

    def ui_dump(self) -> str:
        return self._adb("exec-out", "uiautomator", "dump", "/dev/tty")

    def screencap(self, path: str) -> str:
        data = self._adb_bytes("exec-out", "screencap", "-p")
        with open(path, "wb") as f:
            f.write(data)
        return path

    def window_dump(self) -> str:
        return self._adb("shell", "dumpsys", "window")

    def wm_size(self) -> tuple[int, int]:
        out = self._adb("shell", "wm", "size")
        # "Physical size: 1080x2400"
        wh = out.strip().split(":")[-1].strip()
        w, h = wh.split("x")
        return (int(w), int(h))

    def input_tap(self, x: int, y: int) -> None:
        self._adb("shell", "input", "tap", str(x), str(y))

    def input_text(self, text: str) -> None:
        # Shell-quote so the device shell does not interpret metacharacters.
        # (shlex.quote wraps in single quotes and handles spaces correctly,
        #  superseding the old space->%s substitution.)
        self._adb("shell", "input", "text", shlex.quote(text))

    def input_swipe(self, x1, y1, x2, y2, ms: int = 200) -> None:
        self._adb("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms))

    def input_named_swipe(self, direction: str,
                          distance_pct: float = 0.5, ms: int = 400) -> None:
        if direction not in {"up", "down", "left", "right"}:
            raise ValueError(f"unknown swipe direction: {direction!r}")
        w, h = self.wm_size()
        cx, cy = w // 2, h // 2
        half_x = int(w * distance_pct / 2)
        half_y = int(h * distance_pct / 2)
        if direction == "up":
            self.input_swipe(cx, cy + half_y, cx, cy - half_y, ms)
        elif direction == "down":
            self.input_swipe(cx, cy - half_y, cx, cy + half_y, ms)
        elif direction == "left":
            self.input_swipe(cx + half_x, cy, cx - half_x, cy, ms)
        else:
            self.input_swipe(cx - half_x, cy, cx + half_x, cy, ms)

    def input_long_press(self, x: int, y: int, duration_ms: int = 1000) -> None:
        self.input_swipe(x, y, x, y, duration_ms)

    def input_fling(self, direction: str, velocity: int = 2000) -> None:
        ms = max(50, min(400, 2_000_000 // velocity))
        self.input_named_swipe(direction, distance_pct=0.6, ms=ms)

    def input_key(self, keycode: str) -> None:
        self._adb("shell", "input", "keyevent", keycode)

    def launch(self, package: str) -> None:
        self._adb("shell", "monkey", "-p", shlex.quote(package),
                  "-c", "android.intent.category.LAUNCHER", "1")

    def clipboard_write(self, text: str) -> None:
        self._adb("shell", "service", "call", "clipboard", "2", "s16", shlex.quote(text))

    def clipboard_read(self) -> str:
        return self._adb("shell", "service", "call", "clipboard", "1")

    def intent_start(self, *, action=None, data=None, component=None,
                     extras=None, flags=None) -> None:
        # `adb shell am …` is re-tokenized by the device shell: every value
        # must be quoted, same as input_text/clipboard_write (Finding 7).
        cmd = ["shell", "am", "start"]
        if action:
            cmd += ["-a", shlex.quote(action)]
        if data:
            cmd += ["-d", shlex.quote(data)]
        if component:
            cmd += ["-n", shlex.quote(component)]
        if flags is not None:
            cmd += ["-f", str(flags)]
        for key, val in (extras or {}).items():
            cmd += ["--es", shlex.quote(key), shlex.quote(str(val))]
        self._adb(*cmd)

    def intent_broadcast(self, action: str, *, extras=None) -> None:
        cmd = ["shell", "am", "broadcast", "-a", shlex.quote(action)]
        for key, val in (extras or {}).items():
            cmd += ["--es", shlex.quote(key), shlex.quote(str(val))]
        self._adb(*cmd)

    def packages_list(self, include_system: bool = False) -> list:
        flag = [] if include_system else ["-3"]
        out = self._adb("shell", "pm", "list", "packages", *flag)
        return [
            line.split("package:", 1)[-1].strip()
            for line in out.splitlines()
            if line.startswith("package:")
        ]

    def packages_resolve(self, package: str) -> dict:
        out = self._adb("shell", "dumpsys", "package", package)
        version_name = None
        version_code = None
        launch_activity = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("versionName="):
                version_name = line.split("=", 1)[1]
            elif line.startswith("versionCode="):
                version_code = line.split("=", 1)[1].split()[0]
            elif "Activity" in line and "/" in line and launch_activity is None:
                part = line.strip().split()[-1]
                if "/" in part:
                    launch_activity = part
        return {
            "package": package,
            "version_name": version_name,
            "version_code": version_code,
            "launch_activity": launch_activity,
        }

    def packages_stop(self, package: str) -> None:
        self._adb("shell", "am", "force-stop", package)

    def packages_clear(self, package: str) -> None:
        self._adb("shell", "pm", "clear", package)

    def get_state(self) -> str:
        return self._adb("get-state").strip()

    def adb_version(self) -> str:
        return self._adb("version").strip()

    def devices(self) -> str:
        return self._adb("devices", "-l")

    def wake(self) -> None:
        self._adb("shell", "input", "keyevent", "WAKEUP")

    def keyguard(self) -> bool:
        return ui_parser.parse_keyguard(self.window_dump())

    def lock_state(self) -> dict:
        return ui_parser.parse_lock_state(self.window_dump())

    def mdns_services(self) -> list[str]:
        return ui_parser.parse_mdns_services(self._adb("mdns", "services"))


    def capabilities(self) -> dict:
        return capabilities.make(
            observe_ui_tree=True, observe_screenshot=True,
            act_tap=True, act_type=True, act_key=True,
            launch_app=True, send_intent=True, requires_adb=True,
            write_clipboard=True,
            packages_list=True, packages_stop=True, packages_clear=True,
            intent_start=True, intent_broadcast=True,
        )
