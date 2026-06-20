import hashlib
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
        elements.append(
            {
                "i": i,
                "text": text,
                "id": node.get("resource-id", "") or "",
                "class": node.get("class", "") or "",
                "content_desc": desc,
                "clickable": clickable,
                "bounds": [x1, y1, x2, y2],
                "center": [(x1 + x2) // 2, (y1 + y2) // 2],
            }
        )
        i += 1
    return elements


def screen_hash(elements: list[dict]) -> str:
    h = hashlib.sha1()
    for e in elements:
        h.update(f"{e['text']}|{e['id']}|{e['bounds']}".encode())
    return h.hexdigest()
