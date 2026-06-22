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


# ── Task 2: extract_form ──────────────────────────────────────────────────────

def _make_edittext(i, value, bounds, password=False, focused=False, hint=""):
    x1, y1, x2, y2 = bounds
    el = _make_el(i, value, bounds, clickable=True)
    el["editable"] = True
    el["class"] = "android.widget.EditText"
    el["password"] = password
    el["focused"] = focused
    if hint:
        el["hint_text"] = hint
    return el


def _make_label(i, text, bounds):
    el = _make_el(i, text, bounds, clickable=False)
    el["editable"] = False
    el["class"] = "android.widget.TextView"
    return el


def test_extract_form_finds_field_without_relations():
    label = _make_label(0, "Username", [10, 50, 200, 90])
    field = _make_edittext(1, "alice", [10, 100, 400, 150], hint="Enter username")
    rows = ui_parser.extract_form([label, field])
    assert len(rows) == 1
    assert rows[0]["field_i"] == 1
    assert rows[0]["value"] == "alice"


def test_extract_form_redacts_password_fields():
    field = _make_edittext(0, "secret", [0, 0, 100, 50], password=True)
    rows = ui_parser.extract_form([field])
    assert rows[0]["is_password"] is True
    assert rows[0]["value"] == "[redacted]"


def test_extract_form_finds_label_via_relations():
    label = _make_label(0, "Email", [10, 50, 200, 90])
    field = _make_edittext(1, "", [10, 100, 400, 150])
    relations = {
        "siblings": {0: [1], 1: [0]},
        "parent": {0: None, 1: None},
        "children": {0: [], 1: []},
        "ancestors": {0: [], 1: []},
    }
    rows = ui_parser.extract_form([label, field], relations=relations)
    assert rows[0]["label"] == "Email"


def test_extract_form_marks_focused_field():
    field = _make_edittext(0, "", [0, 0, 100, 50], focused=True)
    rows = ui_parser.extract_form([field])
    assert rows[0]["is_focused"] is True


def test_extract_form_returns_empty_when_no_fields():
    label = _make_label(0, "Title", [0, 0, 100, 30])
    assert ui_parser.extract_form([label]) == []


# ── Task 3: get_focused_field + find_by_text_regex ────────────────────────────

def test_get_focused_field_returns_focused_editable():
    f = _make_edittext(0, "text", [0, 0, 100, 50], focused=True)
    other = _make_el(1, "label", [0, 60, 100, 90])
    assert ui_parser.get_focused_field([other, f])["i"] == 0


def test_get_focused_field_falls_back_to_any_focused():
    el = _make_el(0, "button", [0, 0, 100, 50])
    el["focused"] = True
    assert ui_parser.get_focused_field([el])["i"] == 0


def test_get_focused_field_returns_none_when_none_focused():
    el = _make_el(0, "button", [0, 0, 100, 50])
    assert ui_parser.get_focused_field([el]) is None


def test_find_by_text_regex_matches_substring():
    a = _make_el(0, "Total: $5.00", [0, 0, 100, 50])
    b = _make_el(1, "Balance: $10.00", [0, 60, 100, 110])
    c = _make_el(2, "No match here", [0, 120, 100, 170])
    results = ui_parser.find_by_text_regex([a, b, c], r"\$\d+\.\d+")
    assert len(results) == 2
    assert any(r["i"] == 0 for r in results)
    assert any(r["i"] == 1 for r in results)


def test_find_by_text_regex_empty_when_no_match():
    el = _make_el(0, "nothing", [0, 0, 100, 50])
    assert ui_parser.find_by_text_regex([el], r"\d{4}") == []


def test_find_by_text_regex_preserves_order():
    els = [_make_el(i, f"Item {i}", [0, i*50, 100, i*50+40]) for i in range(5)]
    results = ui_parser.find_by_text_regex(els, r"Item")
    assert [r["i"] for r in results] == [0, 1, 2, 3, 4]


# ── Task 4: get_visible_text_in_region ───────────────────────────────────────

def test_get_visible_text_in_region_returns_overlapping():
    a = _make_el(0, "In region", [10, 100, 400, 200])
    b = _make_el(1, "Partially in", [350, 150, 600, 300])
    c = _make_el(2, "Outside", [500, 400, 900, 500])
    region = (0, 50, 450, 250)
    found = ui_parser.get_visible_text_in_region([a, b, c], region)
    ids = [e["i"] for e in found]
    assert 0 in ids
    assert 1 in ids
    assert 2 not in ids


def test_get_visible_text_in_region_returns_empty_when_none_overlap():
    el = _make_el(0, "Far away", [800, 800, 1000, 900])
    assert ui_parser.get_visible_text_in_region([el], (0, 0, 100, 100)) == []


def test_get_visible_text_in_region_preserves_order():
    els = [_make_el(i, f"el{i}", [i*50, 0, i*50+40, 50]) for i in range(4)]
    found = ui_parser.get_visible_text_in_region(els, (0, 0, 1000, 100))
    assert [e["i"] for e in found] == [0, 1, 2, 3]
