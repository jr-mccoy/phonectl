"""Optional Termux:API provider — discovered at runtime, never a hard dependency."""
from __future__ import annotations

import json as _json
import shutil
import subprocess

from phonectl import capabilities as caps_mod


class TermuxApiProvider:
    def __init__(self, runner=subprocess.run, which=shutil.which) -> None:
        self._runner = runner
        self._which = which

    def is_available(self) -> bool:
        return self._which("termux-battery-status") is not None

    def capabilities(self) -> dict:
        if not self.is_available():
            return caps_mod.make()
        return caps_mod.make(
            read_clipboard=True,
            write_clipboard=True,
            device_battery=True,
            device_wifi_info=True,
            tts_speak=True,
        )

    def _run(self, *cmd: str) -> str:
        res = self._runner(list(cmd), capture_output=True, text=True)
        return res.stdout

    def clipboard_read(self) -> str:
        return self._run("termux-clipboard-get").strip()

    def clipboard_write(self, text: str) -> None:
        self._runner(
            ["termux-clipboard-set"],
            input=text, capture_output=True, text=True,
        )

    def battery_status(self) -> dict:
        raw = self._run("termux-battery-status")
        return _json.loads(raw)

    def wifi_info(self) -> dict:
        raw = self._run("termux-wifi-connectioninfo").strip()
        if not raw:
            return {"ssid": None, "connected": False}
        try:
            return _json.loads(raw)
        except _json.JSONDecodeError:
            return {"ssid": None, "connected": False}

    def tts_speak(self, text: str, *,
                  language: str | None = None,
                  rate: float | None = None) -> None:
        cmd = ["termux-tts-speak"]
        if language is not None:
            cmd += ["-l", language]
        if rate is not None:
            cmd += ["-r", str(rate)]
        cmd.append(text)
        self._run(*cmd)

    def notifications_list(self) -> list:
        raw = self._run("termux-notification-list").strip()
        if not raw:
            return []
        try:
            data = _json.loads(raw)
        except _json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []
