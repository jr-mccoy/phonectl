"""Pure macro-document parse + validation (no I/O, no eval)."""
from __future__ import annotations

from dataclasses import dataclass, field

from phonectl import errors
from phonectl.macro import CONTROL_STEPS, PHONE_VERBS

_TOP_KEYS = {"name", "version", "permissions", "trigger", "conditions",
             "variables", "actions", "policy", "limits"}
_REQUIRED_SUBKEYS = {
    "if": ("condition", "then"),
    "for_each": ("in", "as", "do"),
    "loop": ("do",),
    "retry": ("do",),
    "switch": ("on", "cases"),
    "try": ("do",),
}
_NESTED_LISTS = ("then", "else", "do")


@dataclass(frozen=True)
class Macro:
    name: str
    version: int = 1
    permissions: dict = field(default_factory=dict)
    trigger: dict = None
    conditions: list = field(default_factory=list)
    variables: dict = field(default_factory=dict)
    actions: list = field(default_factory=list)
    policy: dict = field(default_factory=dict)
    limits: dict = field(default_factory=dict)


def _validate_steps(steps, path, errs):
    if not isinstance(steps, list):
        errs.append(f"{path}: expected a list of steps")
        return
    for n, step in enumerate(steps):
        sp = f"{path}[{n}]"
        if not isinstance(step, dict) or "type" not in step:
            errs.append(f"{sp}: each step needs a 'type'")
            continue
        t = step["type"]
        if t not in PHONE_VERBS and t not in CONTROL_STEPS:
            errs.append(f"{sp}: unknown step type {t!r}")
            continue
        for req in _REQUIRED_SUBKEYS.get(t, ()):
            if req not in step:
                errs.append(f"{sp}: step {t!r} requires {req!r}")
        for key in _NESTED_LISTS:
            if key in step:
                _validate_steps(step[key], f"{sp}.{key}", errs)
        if t == "switch":
            for case, body in (step.get("cases") or {}).items():
                _validate_steps(body, f"{sp}.cases.{case}", errs)
            if "default" in step:
                _validate_steps(step["default"], f"{sp}.default", errs)


def validate(doc) -> list:
    errs: list = []
    if not isinstance(doc, dict):
        return ["macro must be an object"]
    if not isinstance(doc.get("name"), str) or not doc.get("name"):
        errs.append("macro requires a non-empty string 'name'")
    for key in doc:
        if key not in _TOP_KEYS:
            errs.append(f"unknown top-level key {key!r}")
    _validate_steps(doc.get("actions", []), "actions", errs)
    return errs


def parse(doc) -> Macro:
    errs = validate(doc)
    if errs:
        raise errors.MacroValidationError("; ".join(errs))
    return Macro(
        name=doc["name"],
        version=int(doc.get("version", 1)),
        permissions=doc.get("permissions", {}),
        trigger=doc.get("trigger"),
        conditions=doc.get("conditions", []),
        variables=doc.get("variables", {}),
        actions=doc.get("actions", []),
        policy=doc.get("policy", {}),
        limits=doc.get("limits", {}),
    )
