"""Macro file loader: JSON (stdlib) + optional YAML extra at the edge only."""
from __future__ import annotations

import json

from phonectl import errors


def _import_yaml():
    try:
        import yaml  # optional extra: phonectl[yaml]
        return yaml
    except Exception:
        return None


def load(path) -> dict:
    text = open(path).read()
    if path.endswith((".yaml", ".yml")):
        yaml = _import_yaml()
        if yaml is None:
            raise errors.MacroValidationError(
                "YAML macros require the optional extra: pip install 'phonectl[yaml]'")
        return yaml.safe_load(text)
    return json.loads(text)
