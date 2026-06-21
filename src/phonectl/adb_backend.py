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

    def input_key(self, keycode: str) -> None:
        self._adb("shell", "input", "keyevent", keycode)

    def launch(self, package: str) -> None:
        self._adb("shell", "monkey", "-p", package,
                  "-c", "android.intent.category.LAUNCHER", "1")

    def get_state(self) -> str:
        return self._adb("get-state").strip()


    def wake(self) -> None:
        self._adb("shell", "input", "keyevent", "WAKEUP")

    def keyguard(self) -> bool:
        return ui_parser.parse_keyguard(self.window_dump())

    def lock_state(self) -> dict:
        return ui_parser.parse_lock_state(self.window_dump())

    def mdns_services(self) -> list[str]:
        return ui_parser.parse_mdns_services(self._adb("mdns", "services"))


    def capabilities(self) -> dict:
        # ADB provides shell/intent/UI powers but not companion-app powers.
        return capabilities.make(
            observe_ui_tree=True, observe_screenshot=True,
            act_tap=True, act_type=True, act_key=True,
            launch_app=True, send_intent=True, requires_adb=True,
        )
