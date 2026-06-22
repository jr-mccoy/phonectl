import hashlib
import re
import xml.etree.ElementTree as ET


def parse_bounds(s: str) -> tuple[int, int, int, int]:
    # "[44,380][1036,520]" -> (44, 380, 1036, 520)
    nums = s.replace("[", " ").replace("]", " ").replace(",", " ").split()
    x1, y1, x2, y2 = (int(n) for n in nums)
    return (x1, y1, x2, y2)


def _is_meaningful(text: str, desc: str, clickable: bool) -> bool:
    return bool(text) or bool(desc) or clickable


def _extract_hierarchy(xml: str) -> str:
    # Devices append a trailing status line after </hierarchy>; slice to the root element.
    start = xml.find("<hierarchy")
    end = xml.rfind("</hierarchy>")
    if start != -1 and end != -1:
        return xml[start : end + len("</hierarchy>")]
    return xml


def parse_elements(xml: str) -> list[dict]:
    root = ET.fromstring(_extract_hierarchy(xml))
    elements: list[dict] = []
    i = 0
    for node in root.iter("node"):
        text = node.get("text", "") or ""
        desc = node.get("content-desc", "") or ""
        clickable = node.get("clickable", "false") == "true"
        if not _is_meaningful(text, desc, clickable):
            continue
        x1, y1, x2, y2 = parse_bounds(node.get("bounds", "[0,0][0,0]"))

        def _b(attr, default="false"):
            return node.get(attr, default) == "true"

        cls = node.get("class", "") or ""
        el = {
            "i": i,
            "text": text,
            "id": node.get("resource-id", "") or "",
            "class": cls,
            "content_desc": desc,
            "clickable": clickable,
            "enabled": _b("enabled", "true"),
            "focused": _b("focused"),
            "checkable": _b("checkable"),
            "checked": _b("checked"),
            "scrollable": _b("scrollable"),
            "long_clickable": _b("long-clickable"),
            "password": _b("password"),
            "selected": _b("selected"),
            "editable": _b("editable") or "EditText" in cls,
            "package": node.get("package", "") or "",
            "bounds": [x1, y1, x2, y2],
            "center": [(x1 + x2) // 2, (y1 + y2) // 2],
        }
        if node.get("hint_text") is not None:
            el["hint_text"] = node.get("hint_text") or ""
        if node.get("error_text") is not None:
            el["error_text"] = node.get("error_text") or ""
        elements.append(el)
        i += 1
    return elements

def screen_hash(elements: list[dict]) -> str:
    h = hashlib.sha1()
    for e in elements:
        h.update(f"{e['text']}|{e['id']}|{e['bounds']}".encode())
    return h.hexdigest()


def _iter_tree(root):
    counter = 0

    def visit(node, parent):
        nonlocal counter
        text = node.get("text", "") or ""
        desc = node.get("content-desc", "") or ""
        clickable = node.get("clickable", "false") == "true"
        meaningful = _is_meaningful(text, desc, clickable)
        idx = counter if meaningful else None
        if meaningful:
            counter += 1
        item = {"i": idx, "class": node.get("class", "") or "", "children": [], "_parent": parent}
        for child in node.findall("node"):
            item["children"].append(visit(child, idx if idx is not None else parent))
        return item

    nodes = [visit(child, None) for child in root.findall("node")]
    if len(nodes) == 1:
        return nodes[0]
    return {"i": None, "class": root.get("class", "hierarchy") or "hierarchy", "children": nodes, "_parent": None}


def build_tree(xml: str) -> dict:
    """Build a hierarchy-preserving tree keyed to parse_elements indices."""
    root = ET.fromstring(_extract_hierarchy(xml))
    tree = _iter_tree(root)

    def strip_private(node):
        node.pop("_parent", None)
        for child in node["children"]:
            strip_private(child)
        return node

    return strip_private(tree)


def parse_relations(xml: str) -> dict:
    """Return parent/children/siblings/ancestors maps for meaningful elements."""
    root = ET.fromstring(_extract_hierarchy(xml))
    tree = _iter_tree(root)
    parent = {}
    children = {}
    nodes = {}

    def collect(node):
        idx = node["i"]
        if idx is not None:
            nodes[idx] = node
            parent[idx] = node.get("_parent")
            children[idx] = []
        for child in node["children"]:
            collect(child)
            if idx is not None and child["i"] is not None:
                children[idx].append(child["i"])

    collect(tree)
    # include meaningful descendants under nearest meaningful parent
    for idx in list(nodes):
        p = parent[idx]
        if p is not None and idx not in children.setdefault(p, []):
            children[p].append(idx)
    siblings = {idx: [] for idx in nodes}
    for idx in nodes:
        p = parent[idx]
        siblings[idx] = [other for other, op in parent.items() if op == p and other != idx]
    ancestors = {}
    for idx in nodes:
        chain = []
        p = parent[idx]
        while p is not None:
            chain.append(p)
            p = parent.get(p)
        ancestors[idx] = chain
    return {"parent": parent, "children": children, "siblings": siblings, "ancestors": ancestors}


def extract_list(elements: list, *, container_i=None, relations=None) -> list:
    """Return child elements of a scrollable list container using spatial containment."""
    if not elements:
        return []
    container = None
    if container_i is not None:
        container = next((e for e in elements if e["i"] == container_i), None)
    if container is None:
        container = next((e for e in elements if e.get("scrollable")), None)
    if container is None:
        return []
    x1, y1, x2, y2 = container["bounds"]
    return [
        e for e in elements
        if e["i"] != container["i"]
        and x1 <= e["center"][0] <= x2
        and y1 <= e["center"][1] <= y2
    ]


_SELECTOR_KEYS = {
    "text", "text_regex", "content_desc", "resource_id", "id", "class", "ancestor_text",
    "sibling_text", "bounds_near", "nth_match", "clickable", "enabled", "focused", "checkable",
    "checked", "scrollable", "long_clickable", "password", "selected", "editable",
}


def match_selector(elements: list[dict], selector: dict, relations: dict | None = None) -> list[int]:
    """Return ranked matching element indices; relation predicates need relations or do not match."""
    unknown = set(selector) - _SELECTOR_KEYS
    if unknown:
        raise ValueError(f"unknown selector key(s): {', '.join(sorted(unknown))}")
    by_i = {e["i"]: e for e in elements}

    def text_matches(idx, value):
        return by_i.get(idx, {}).get("text") == value

    def ok(e):
        for k, v in selector.items():
            if k == "nth_match":
                continue
            if k == "text" and e.get("text") != v: return False
            if k == "text_regex" and not re.search(v, e.get("text", "")): return False
            if k == "content_desc" and e.get("content_desc") != v: return False
            if k in ("resource_id", "id") and e.get("id") != v: return False
            if k == "class" and e.get("class") != v: return False
            if k in {"clickable","enabled","focused","checkable","checked","scrollable","long_clickable","password","selected","editable"} and e.get(k) is not v: return False
            if k == "bounds_near":
                x1,y1,x2,y2 = v; cx, cy = e.get("center", [0,0])
                if not (x1 <= cx <= x2 and y1 <= cy <= y2): return False
            if k == "sibling_text":
                if relations is None: return False
                if not any(text_matches(i, v) for i in relations.get("siblings", {}).get(e["i"], [])): return False
            if k == "ancestor_text":
                if relations is None: return False
                if not any(text_matches(i, v) for i in relations.get("ancestors", {}).get(e["i"], [])): return False
        return True

    ranked = [e for e in elements if ok(e)]
    def rank(e):
        score = 0
        if "text" in selector and e.get("text") == selector["text"]: score += 100
        if "text_regex" in selector and re.search(selector["text_regex"], e.get("text", "")): score += 50
        if "content_desc" in selector or "resource_id" in selector or "id" in selector: score += 25
        if e.get("clickable"): score += 5
        return (-score, e["i"])
    ranked.sort(key=rank)
    ids = [e["i"] for e in ranked]
    if "nth_match" in selector:
        n = selector["nth_match"]
        return [ids[n]] if 0 <= n < len(ids) else []
    return ids


_ROTATION_RE = re.compile(r"<hierarchy[^>]*\brotation=[\"'](\d+)[\"']")
_KEYGUARD_PATTERNS = ("mDreamingLockscreen=true", "mShowingLockscreen=true")
_HOSTPORT_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3}:\d+)")


def is_error_dump(text: str) -> bool:
    """True when uiautomator returned a status/error line instead of XML."""
    stripped = (text or "").strip()
    if not stripped:
        return True
    if stripped.startswith("ERROR:"):
        return True
    return "<hierarchy" not in stripped


def parse_rotation(xml: str) -> int:
    """Read <hierarchy rotation="N">; 0 when absent/unparseable."""
    m = _ROTATION_RE.search(xml or "")
    if not m:
        return 0
    try:
        return int(m.group(1))
    except ValueError:
        return 0


def parse_keyguard(window_dump: str) -> bool:
    """True when `dumpsys window` reports the lock screen / keyguard is showing."""
    text = window_dump or ""
    for pat in _KEYGUARD_PATTERNS:
        if pat in text:
            return True
    for line in text.splitlines():
        if "KeyguardServiceDelegate" in line and "showing=true" in line:
            return True
    return False


def parse_lock_state(window_dump: str) -> dict:
    """Structured lock state. can_act is True only when unlocked."""
    text = window_dump or ""
    if not parse_keyguard(text):
        return {"lock_state": "unlocked", "can_act": True, "recommended_user_action": None}
    secure = ("secure=true" in text) or ("KeyguardSecure=true" in text)
    if secure:
        return {"lock_state": "locked_secure", "can_act": False,
                "recommended_user_action": "Unlock the phone manually."}
    return {"lock_state": "locked_swipe_only", "can_act": False,
            "recommended_user_action": "Swipe up to dismiss the lock screen, then retry."}


def parse_mdns_services(text: str) -> list[str]:
    """Parse `adb mdns services` output into ip:port candidate strings."""
    out: list[str] = []
    for line in (text or "").splitlines():
        m = _HOSTPORT_RE.search(line)
        if m:
            out.append(m.group(1))
    return out
