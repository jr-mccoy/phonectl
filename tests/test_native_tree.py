from phonectl import native_tree, ui_parser

NATIVE = {
    "windows": [
        {"id": 1, "type": "application", "package": "com.example",
         "nodes": [
             {"node_id": "n1", "text": "Wi-Fi", "class": "android.widget.TextView",
              "content_desc": "", "bounds": [44, 380, 1036, 520],
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


def test_to_compat_xml_escapes_special_chars():
    native = {"windows": [{"id": 1, "type": "application", "package": "x",
              "nodes": [{"node_id": "n", "text": 'a & b "q"', "class": "T",
                         "content_desc": "", "bounds": [0, 0, 1, 1]}]}]}
    xml = native_tree.to_compat_xml(native)
    assert "&amp;" in xml  # raw '&' never leaks into the document
