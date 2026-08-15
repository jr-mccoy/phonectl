"""A scripted, device-free Backend for the eval harness.

``ScriptedBackend`` implements the ``droidjig.backend.Backend`` protocol over a fixed list of UI
screens. Navigational actions (tap, swipe) advance a cursor to the next screen, modelling a linear
flow; text and key input are recorded but do not advance. Everything the real observe->act pipeline
reads — ``ui_dump``/``window_dump``/``wm_size`` — is served from the current screen, so
``runtime.run_action`` runs end to end with no ADB and no companion.

Generalizes the ad-hoc ``CannedBackend``/``FakeBackend`` test doubles into one reusable simulator.
"""
from __future__ import annotations

from droidjig import capabilities


def screen(*nodes: str, rotation: int = 0) -> str:
    """Wrap uiautomator node fragments in a hierarchy document."""
    return (f"<?xml version='1.0'?><hierarchy rotation=\"{rotation}\">"
            + "".join(nodes) + "</hierarchy>")


def node(*, text="", rid="", cls="android.widget.TextView", bounds="[0,0][1080,200]",
         clickable=True, password=False, desc="", scrollable=False, extra="") -> str:
    """One uiautomator node fragment with the attributes ui_parser reads."""
    return (f'<node index="0" text="{text}" resource-id="{rid}" class="{cls}" '
            f'content-desc="{desc}" clickable="{str(clickable).lower()}" '
            f'scrollable="{str(scrollable).lower()}" '
            f'password="{str(password).lower()}" bounds="{bounds}"{(" " + extra) if extra else ""}/>')


def _focus_line(package: str) -> str:
    return f"mCurrentFocus=Window{{a b {package}/.MainActivity}}"


DEFAULT_CAPS = dict(
    observe_ui_tree=True, act_tap=True, act_type=True, act_key=True,
    launch_app=True, observe_screenshot=True, requires_adb=True,
)


class ScriptedBackend:
    """Device-free Backend. ``screens`` is a list of XML strings (or ``(xml, package)`` tuples);
    tap/swipe advance the cursor (clamped at the last screen)."""

    def __init__(self, screens, *, package="com.eval.app", caps=None, locked=False):
        self._screens = [s if isinstance(s, tuple) else (s, package) for s in screens]
        if not self._screens:
            raise ValueError("ScriptedBackend needs at least one screen")
        self._i = 0
        self._locked = locked
        self.serial = "eval:0"
        self.taps: list = []
        self.texts: list = []
        self.keys: list = []
        self.swipes: list = []
        self.launched: list = []
        self._caps = capabilities.make(**(caps or DEFAULT_CAPS))

    # ── observation ─────────────────────────────────────────────────────────
    def ui_dump(self) -> str:
        return self._screens[self._i][0]

    def window_dump(self) -> str:
        pkg = self._screens[self._i][1]
        if self._locked:
            return ("mCurrentFocus=Window{a b StatusBar}\n"
                    "mDreamingLockscreen=true\nmShowingLockscreen=true")
        return _focus_line(pkg)

    def wm_size(self):
        return (1080, 2400)

    def screencap(self, path):
        return path

    def get_state(self):
        return "device"

    def capabilities(self):
        return self._caps

    # ── actuation ───────────────────────────────────────────────────────────
    def _advance(self):
        self._i = min(self._i + 1, len(self._screens) - 1)

    def input_tap(self, x, y):
        self.taps.append((x, y))
        self._advance()

    def input_swipe(self, x1, y1, x2, y2, ms=200):
        self.swipes.append((x1, y1, x2, y2, ms))
        self._advance()

    def input_text(self, text):
        self.texts.append(text)

    def input_key(self, keycode):
        self.keys.append(keycode)

    def launch(self, package):
        self.launched.append(package)

    # ── introspection for scenarios ─────────────────────────────────────────
    @property
    def current_index(self) -> int:
        return self._i
