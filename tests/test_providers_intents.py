from phonectl.providers.intents import IntentProvider
from phonectl.providers.registry import ProviderRegistry
from phonectl import capabilities


class FakeIntentProv:
    _started = None
    _broadcast = None

    def capabilities(self):
        return capabilities.make(intent_start=True, intent_broadcast=True, requires_adb=True,
                                 act_tap=True, observe_ui_tree=True, launch_app=True,
                                 act_type=True, act_key=True)

    def intent_start(self, **kwargs):
        FakeIntentProv._started = kwargs

    def intent_broadcast(self, action, **kwargs):
        FakeIntentProv._broadcast = (action, kwargs)

    def get_state(self): return "device"
    def ui_dump(self): return "<hierarchy></hierarchy>"
    def window_dump(self): return ""
    def wm_size(self): return (1080, 2400)
    def input_tap(self, x, y): pass
    def input_text(self, t): pass
    def input_swipe(self, *a): pass
    def input_key(self, k): pass
    def launch(self, p): pass
    def screencap(self, p): return p


def test_start_raises_unavailable_when_no_provider():
    r = ProviderRegistry([])
    ip = IntentProvider(r)
    env = ip.start(action="x", build=lambda cfg: (r, None, None), yes=True, cfg={})
    assert env["ok"] is False
    assert env["error"]["code"] == "capability_unavailable"


def test_intent_provider_is_constructable():
    r = ProviderRegistry([FakeIntentProv()])
    ip = IntentProvider(r)
    assert ip is not None
