"""Pure native-JSON -> uiautomator-compatible XML conversion. No I/O, no subprocess."""
from __future__ import annotations

from xml.sax.saxutils import quoteattr

_FLAGS = ("checkable", "checked", "clickable", "enabled", "focused", "scrollable", "password")


def _attr(name: str, value: str) -> str:
    return f"{name}={quoteattr(value)}"


def _node_xml(node: dict) -> str:
    l, t, r, b = node.get("bounds", [0, 0, 0, 0])
    parts = [
        _attr("text", str(node.get("text", "") or "")),
        _attr("resource-id", str(node.get("resource_id", "") or "")),
        _attr("class", str(node.get("class", "") or "")),
        _attr("content-desc", str(node.get("content_desc", "") or "")),
        _attr("bounds", f"[{l},{t}][{r},{b}]"),
        # Companion-native extras: node-id targets semantic/set-text actions, actions
        # says which the node supports. Always emitted (possibly empty) so consumers
        # can distinguish a companion tree from an ADB uiautomator dump.
        _attr("node-id", str(node.get("node_id", "") or "")),
        _attr("actions", ",".join(node.get("actions", []) or [])),
    ]
    for flag in _FLAGS:
        parts.append(_attr(flag, "true" if node.get(flag) else "false"))
    return "<node " + " ".join(parts) + " />"


def to_compat_xml(native: dict) -> str:
    nodes = []
    for window in native.get("windows", []):
        for node in window.get("nodes", []):
            nodes.append(_node_xml(node))
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<hierarchy rotation="0">' + "".join(nodes) + "</hierarchy>")
