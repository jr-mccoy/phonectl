from droidjig.providers.packages import PackageProvider
from droidjig.providers.registry import ProviderRegistry
from droidjig import capabilities


class FakePkgProv:
    def capabilities(self):
        return capabilities.make(packages_list=True, packages_stop=True,
                                 packages_clear=True, requires_adb=True,
                                 act_tap=True, observe_ui_tree=True,
                                 launch_app=True, act_type=True, act_key=True)

    def packages_list(self, include_system=False):
        return ["com.a", "com.b"]

    def packages_resolve(self, pkg):
        return {"package": pkg, "version_name": "1.0", "version_code": "1", "launch_activity": None}

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
    def packages_stop(self, p): pass
    def packages_clear(self, p): pass


def test_list_packages_returns_ok_envelope():
    r = ProviderRegistry([FakePkgProv()])
    pp = PackageProvider(r)
    env = pp.list_packages()
    assert env["ok"] is True
    assert "com.a" in env["data"]["packages"]


def test_resolve_returns_ok_envelope():
    r = ProviderRegistry([FakePkgProv()])
    pp = PackageProvider(r)
    env = pp.resolve("com.a")
    assert env["ok"] is True
    assert env["data"]["package"] == "com.a"


def test_list_returns_unavailable_when_no_provider():
    r = ProviderRegistry([])
    pp = PackageProvider(r)
    env = pp.list_packages()
    assert env["ok"] is False
    assert env["error"]["code"] == "capability_unavailable"
