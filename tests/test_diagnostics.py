import json
import zipfile

from droidjig import capabilities, diagnostics


def test_redact_masks_sensitive_keys_only():
    cfg = {"serial": "127.0.0.1:41000", "mode": "auto", "last_port": "41000",
           "pairing_code": "482913", "api_token": "abc", "nested": {"secret": "s", "ok": 1}}
    red = diagnostics.redact_config(cfg)
    assert red["serial"] == "127.0.0.1:41000"
    assert red["mode"] == "auto"
    assert red["last_port"] == "41000"
    assert red["pairing_code"] == "***"
    assert red["api_token"] == "***"
    assert red["nested"]["secret"] == "***"
    assert red["nested"]["ok"] == 1
    assert cfg["pairing_code"] == "482913"


class DiagBackend:
    serial = "127.0.0.1:41000"
    def capabilities(self): return capabilities.make(requires_adb=True, act_tap=True)
    def get_state(self): return "device"
    def adb_version(self): return "Android Debug Bridge version 1.0.41"
    def devices(self): return "List of devices attached\n127.0.0.1:41000 device\n"
    def mdns_services(self): return ["127.0.0.1:41000"]


def test_collect_includes_redacted_config_and_capabilities(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    (tmp_path / "actions.jsonl").write_text(json.dumps({"ts": 1, "verb": "tap", "app": "com.x", "hash": "h", "text": "secret"}) + "\n")
    data = diagnostics.collect(DiagBackend(), {"serial": "127.0.0.1:41000", "pairing_code": "482913"})
    assert data["config"]["pairing_code"] == "***"
    assert data["capabilities"]["requires_adb"] is True
    assert data["state"] == "device"
    assert "1.0.41" in data["adb_version"]
    assert data["mdns"] == ["127.0.0.1:41000"]
    assert data["host_shim"] is False
    assert data["audit_tail"][-1]["verb"] == "tap"
    assert "text" not in data["audit_tail"][-1]


def test_bundle_writes_zip_with_manifest_and_blobs(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))
    out = str(tmp_path / "diag.zip")
    ret = diagnostics.bundle(out, DiagBackend(), {"serial": "127.0.0.1:41000", "pairing_code": "482913"})
    assert ret == out
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert "manifest.json" in names
        assert "adb-version.txt" in names and "adb-devices.txt" in names
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["config"]["pairing_code"] == "***"
