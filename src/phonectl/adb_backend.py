import shlex
import subprocess

from phonectl import capabilities, ui_parser

class AdbBackend:
    def __init__(self, serial=None, runner=subprocess.run):
        self.serial = serial
        self._runner = runner

    def _base(self) -> list[str]:
        return ["adb", "-s", self.serial] if self.serial else ["adb"]

    def _adb(self, *args: str) -> str:
        cmd = self._base() + list(args)
        res = self._runner(cmd, capture_output=True, text=True)
        return res.stdout

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
