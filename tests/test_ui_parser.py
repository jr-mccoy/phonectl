from pathlib import Path
from phonectl import ui_parser

FIXTURE = (Path(__file__).parent / "fixtures" / "settings_dump.xml").read_text()

def test_parse_bounds():
    assert ui_parser.parse_bounds("[44,380][1036,520]") == (44, 380, 1036, 520)

def test_parse_elements_filters_and_indexes():
    els = ui_parser.parse_elements(FIXTURE)
    # FrameLayout (no text/desc, not clickable) excluded; 4 meaningful nodes kept
    assert [e["text"] for e in els] == ["Settings", "Wi-Fi", "Bluetooth", ""]
    assert [e["i"] for e in els] == [0, 1, 2, 3]

def test_parse_elements_center_and_flags():
    els = ui_parser.parse_elements(FIXTURE)
    wifi = els[1]
    assert wifi["bounds"] == [44, 380, 1036, 520]
    assert wifi["center"] == [540, 450]
    assert wifi["clickable"] is True
    search = els[3]
    assert search["content_desc"] == "Search"
    assert search["clickable"] is True

def test_screen_hash_stable_and_sensitive():
    els = ui_parser.parse_elements(FIXTURE)
    h1 = ui_parser.screen_hash(els)
    # stable across re-parse
    assert h1 == ui_parser.screen_hash(ui_parser.parse_elements(FIXTURE))
    # sensitive to text changes
    changed_text = [dict(e) for e in els]
    changed_text[1] = {**changed_text[1], "text": "Wi-Fi (off)"}
    assert ui_parser.screen_hash(changed_text) != h1
    # sensitive to id changes
    changed_id = [dict(e) for e in els]
    changed_id[1] = {**changed_id[1], "id": "android:id/summary"}
    assert ui_parser.screen_hash(changed_id) != h1
    # sensitive to bounds changes
    changed_bounds = [dict(e) for e in els]
    changed_bounds[1] = {**changed_bounds[1], "bounds": [0, 0, 1, 1]}
    assert ui_parser.screen_hash(changed_bounds) != h1

def test_parse_elements_tolerates_device_trailing_line():
    # `uiautomator dump /dev/tty` appends a status line after </hierarchy>
    noisy = FIXTURE + "\nUI hierchary dumped to: /dev/tty\n"
    els = ui_parser.parse_elements(noisy)
    assert [e["text"] for e in els] == ["Settings", "Wi-Fi", "Bluetooth", ""]

RICH_NODE = (
    "<?xml version='1.0'?><hierarchy rotation=\"0\">"
    "<node index=\"0\" text=\"Wi-Fi\" resource-id=\"android:id/title\" "
    "class=\"android.widget.Switch\" content-desc=\"\" package=\"com.android.settings\" "
    "clickable=\"true\" enabled=\"true\" focused=\"false\" checkable=\"true\" "
    "checked=\"true\" scrollable=\"false\" long-clickable=\"true\" password=\"false\" "
    "selected=\"false\" bounds=\"[44,380][1036,520]\"/>"
    "<node index=\"1\" text=\"\" resource-id=\"x:id/field\" class=\"android.widget.EditText\" "
    "content-desc=\"Search\" package=\"com.android.settings\" clickable=\"true\" "
    "enabled=\"false\" password=\"true\" bounds=\"[0,600][1080,700]\"/>"
    "</hierarchy>")


def test_parse_elements_captures_richer_metadata():
    els = ui_parser.parse_elements(RICH_NODE)
    sw = els[0]
    assert sw["enabled"] is True
    assert sw["checkable"] is True
    assert sw["checked"] is True
    assert sw["long_clickable"] is True
    assert sw["package"] == "com.android.settings"
    field = els[1]
    assert field["editable"] is True
    assert field["password"] is True
    assert field["enabled"] is False


def test_existing_fields_and_hash_unchanged():
    els = ui_parser.parse_elements(RICH_NODE)
    assert els[0]["text"] == "Wi-Fi"
    assert els[0]["center"] == [540, 450]
    h = ui_parser.screen_hash(els)
    assert isinstance(h, str) and len(h) == 40


NESTED = (
    "<?xml version='1.0'?><hierarchy rotation=\"0\">"
    "<node class=\"android.widget.FrameLayout\" bounds=\"[0,0][1080,2400]\">"
    "  <node text=\"Network\" class=\"T\" clickable=\"true\" bounds=\"[0,0][500,100]\"/>"
    "  <node class=\"android.widget.LinearLayout\" bounds=\"[0,100][1080,300]\">"
    "    <node text=\"Wi-Fi\" class=\"T\" clickable=\"true\" bounds=\"[0,100][500,200]\"/>"
    "    <node text=\"Bluetooth\" class=\"T\" clickable=\"true\" bounds=\"[0,200][500,300]\"/>"
    "  </node>"
    "</node></hierarchy>")


def test_build_tree_preserves_structure():
    tree = ui_parser.build_tree(NESTED)
    assert tree["class"].endswith("FrameLayout")
    classes = [c["class"] for c in tree["children"]]
    assert any(c.endswith("LinearLayout") for c in classes)


def test_parse_relations_parent_children_siblings():
    rel = ui_parser.parse_relations(NESTED)
    els = ui_parser.parse_elements(NESTED)
    assert [e["text"] for e in els] == ["Network", "Wi-Fi", "Bluetooth"]
    assert 2 in rel["siblings"][1]
    assert 1 in rel["siblings"][2]
    assert rel["parent"][1] == rel["parent"][2]


def _els():
    return ui_parser.parse_elements(NESTED)


def test_match_exact_text():
    assert ui_parser.match_selector(_els(), {"text": "Wi-Fi"}) == [1]


def test_match_text_regex():
    got = ui_parser.match_selector(_els(), {"text_regex": "^(Wi-?Fi|Bluetooth)$"})
    assert set(got) == {1, 2}


def test_match_flag_clickable_and_class():
    got = ui_parser.match_selector(_els(), {"clickable": True, "class": "T"})
    assert set(got) == {0, 1, 2}


def test_match_nth_picks_positionally():
    got = ui_parser.match_selector(_els(), {"text_regex": ".+", "nth_match": 1})
    assert got == [1]


def test_match_sibling_text_uses_relations():
    rel = ui_parser.parse_relations(NESTED)
    got = ui_parser.match_selector(_els(), {"text": "Wi-Fi", "sibling_text": "Bluetooth"}, relations=rel)
    assert got == [1]


def test_no_match_returns_empty():
    assert ui_parser.match_selector(_els(), {"text": "Nope"}) == []


def test_unknown_selector_key_raises():
    import pytest
    with pytest.raises(ValueError):
        ui_parser.match_selector(_els(), {"txt": "Wi-Fi"})


def test_is_error_dump_detects_idle_state_error():
    assert ui_parser.is_error_dump("ERROR: could not get idle state.") is True


def test_is_error_dump_detects_non_xml_and_empty():
    assert ui_parser.is_error_dump("null root node returned by UiTestAutomationBridge.") is True
    assert ui_parser.is_error_dump("") is True


def test_is_error_dump_false_for_real_hierarchy_even_with_trailing_status():
    good = "<?xml version='1.0'?><hierarchy rotation='0'><node/></hierarchy>"
    assert ui_parser.is_error_dump(good) is False
    noisy = good + "\nUI hierchary dumped to: /dev/tty"
    assert ui_parser.is_error_dump(noisy) is False


def test_parse_rotation_reads_attribute_and_defaults_zero():
    assert ui_parser.parse_rotation("<hierarchy rotation='1'><node/></hierarchy>") == 1
    assert ui_parser.parse_rotation('<hierarchy rotation="3"></hierarchy>') == 3
    assert ui_parser.parse_rotation("<hierarchy><node/></hierarchy>") == 0
    assert ui_parser.parse_rotation("garbage") == 0


def test_parse_keyguard_detects_showing_and_unlocked():
    assert ui_parser.parse_keyguard("  mDreamingLockscreen=true\n") is True
    assert ui_parser.parse_keyguard("KeyguardServiceDelegate{showing=true secure=true}") is True
    assert ui_parser.parse_keyguard("  mDreamingLockscreen=false\n  mCurrentFocus=...") is False


def test_parse_lock_state_locked_states_and_unlocked():
    assert ui_parser.parse_lock_state("  mDreamingLockscreen=false\n") == {
        "lock_state": "unlocked", "can_act": True, "recommended_user_action": None,
    }
    secure = ui_parser.parse_lock_state("KeyguardServiceDelegate{showing=true secure=true}")
    assert secure["lock_state"] == "locked_secure"
    assert secure["can_act"] is False
    assert "nlock" in secure["recommended_user_action"]
    swipe = ui_parser.parse_lock_state(
        "  mDreamingLockscreen=true\n  KeyguardServiceDelegate{showing=true secure=false}"
    )
    assert swipe["lock_state"] == "locked_swipe_only"
    assert swipe["can_act"] is False


MDNS_OUT = """List of discovered mdns services
adb-39FA-coo1\t_adb-tls-connect._tcp\t192.168.1.42:43210
adb-7C2B-zz9q\t_adb-tls-pairing._tcp\t192.168.1.42:37115
"""


def test_parse_mdns_services_extracts_host_ports():
    assert ui_parser.parse_mdns_services(MDNS_OUT) == ["192.168.1.42:43210", "192.168.1.42:37115"]


def test_parse_mdns_services_empty_when_none_found():
    assert ui_parser.parse_mdns_services("List of discovered mdns services\n") == []
    assert ui_parser.parse_mdns_services("") == []


# ── Task 1: extract_list ──────────────────────────────────────────────────────

def _make_el(i, text, bounds, scrollable=False, clickable=True):
    x1, y1, x2, y2 = bounds
    return {
        "i": i, "text": text, "id": "", "class": "android.view.View",
        "content_desc": "", "clickable": clickable, "enabled": True,
        "focused": False, "checkable": False, "checked": False,
        "scrollable": scrollable, "long_clickable": False, "password": False,
        "selected": False, "editable": False, "package": "",
        "bounds": list(bounds), "center": [(x1+x2)//2, (y1+y2)//2],
    }


def test_extract_list_finds_children_of_scrollable_container():
    container = _make_el(0, "", [0, 100, 1080, 900], scrollable=True, clickable=False)
    row1 = _make_el(1, "Row A", [10, 120, 1070, 180])
    row2 = _make_el(2, "Row B", [10, 190, 1070, 250])
    outside = _make_el(3, "Outside", [0, 0, 1080, 90])
    elements = [container, row1, row2, outside]
    rows = ui_parser.extract_list(elements)
    texts = [r["text"] for r in rows]
    assert "Row A" in texts
    assert "Row B" in texts
    assert "Outside" not in texts
    assert "" not in texts  # container itself excluded


def test_extract_list_with_explicit_container_i():
    container = _make_el(0, "", [0, 100, 1080, 900], scrollable=True, clickable=False)
    row1 = _make_el(1, "Item 1", [10, 120, 1070, 180])
    elements = [container, row1]
    rows = ui_parser.extract_list(elements, container_i=0)
    assert any(r["text"] == "Item 1" for r in rows)


def test_extract_list_returns_empty_when_no_scrollable():
    el = _make_el(0, "plain text", [0, 0, 100, 50])
    assert ui_parser.extract_list([el]) == []


def test_extract_list_returns_empty_for_empty_input():
    assert ui_parser.extract_list([]) == []
