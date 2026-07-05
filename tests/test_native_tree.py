from phonectl import native_tree, ui_parser

NATIVE = {
    "windows": [
        {"id": 1, "type": "application", "package": "com.example",
         "nodes": [
             {"node_id": "n1", "text": "Wi-Fi", "class": "android.widget.TextView",
              "content_desc": "", "bounds": [44, 380, 1036, 520],
              "resource_id": "com.example:id/wifi", "actions": ["click", "long_click"],
              "clickable": True, "enabled": True, "scrollable": False, "password": False},
             {"node_id": "n2", "text": "", "class": "android.widget.EditText",
              "content_desc": "Search", "bounds": [0, 100, 1080, 200],
              "clickable": True, "enabled": True, "scrollable": False, "password": True},
         ]},
    ]
}


def test_to_compat_xml_is_parseable_by_ui_parser():
    xml = native_tree.to_compat_xml(NATIVE)
    elements = ui_parser.parse_elements(xml)
    texts = [e.get("text") for e in elements]
    assert "Wi-Fi" in texts
    assert ui_parser.screen_hash(elements)  # non-empty stable hash


def test_to_compat_xml_maps_bounds_and_flags():
    xml = native_tree.to_compat_xml(NATIVE)
    assert 'bounds="[44,380][1036,520]"' in xml
    assert 'password="true"' in xml
    assert 'content-desc="Search"' in xml


def test_to_compat_xml_carries_node_id_resource_id_and_actions():
    # Without these the companion path blinds id-selectors and makes semantic node
    # actions unreachable from snapshots — the tree's whole advantage over ADB.
    xml = native_tree.to_compat_xml(NATIVE)
    assert 'node-id="n1"' in xml
    assert 'resource-id="com.example:id/wifi"' in xml
    assert 'actions="click,long_click"' in xml
    elements = ui_parser.parse_elements(xml)
    wifi = next(e for e in elements if e["text"] == "Wi-Fi")
    assert wifi["id"] == "com.example:id/wifi"
    assert wifi["node_id"] == "n1"
    assert wifi["actions"] == ["click", "long_click"]


def test_to_compat_xml_tolerates_absent_resource_id_and_actions():
    # Pre-resource_id companion payloads (and nodes with no actions) still convert.
    xml = native_tree.to_compat_xml(NATIVE)
    search = next(e for e in ui_parser.parse_elements(xml) if e["content_desc"] == "Search")
    assert search["id"] == ""
    assert search["node_id"] == "n2"
    assert search["actions"] == []


def test_to_compat_xml_escapes_special_chars():
    native = {"windows": [{"id": 1, "type": "application", "package": "x",
              "nodes": [{"node_id": "n", "text": 'a & b "q"', "class": "T",
                         "content_desc": "", "bounds": [0, 0, 1, 1]}]}]}
    xml = native_tree.to_compat_xml(native)
    assert "&amp;" in xml  # raw '&' never leaks into the document
