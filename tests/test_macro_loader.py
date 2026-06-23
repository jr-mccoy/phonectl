import json
import pytest

from phonectl import errors
from phonectl.macro import loader


def test_load_json(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"name": "m", "actions": []}))
    assert loader.load(str(p))["name"] == "m"


def test_load_yaml_without_extra_raises(tmp_path, monkeypatch):
    p = tmp_path / "m.yaml"
    p.write_text("name: m\nactions: []\n")
    monkeypatch.setattr(loader, "_import_yaml", lambda: None)
    with pytest.raises(errors.MacroValidationError):
        loader.load(str(p))
