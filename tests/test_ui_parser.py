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
