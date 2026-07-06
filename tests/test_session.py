import pytest
from phonectl import ui_parser, errors
from phonectl.session import Session

NESTED = (
    "<?xml version='1.0'?><hierarchy rotation=\"0\">"
    "<node class=\"F\" bounds=\"[0,0][1080,2400]\">"
    "<node text=\"Wi-Fi\" class=\"T\" clickable=\"true\" bounds=\"[0,100][500,200]\"/>"
    "<node text=\"Bluetooth\" class=\"T\" clickable=\"true\" bounds=\"[0,200][500,300]\"/>"
    "</node></hierarchy>")


def _snap():
    els = ui_parser.parse_elements(NESTED)
    return {"elements": els, "relations": ui_parser.parse_relations(NESTED),
            "hash": ui_parser.screen_hash(els)}


def test_find_returns_candidate_indices():
    s = Session(); s.set_snapshot(_snap())
    assert s.find({"text": "Bluetooth"}) == [1]


def test_resolve_selector_returns_center():
    s = Session(); s.set_snapshot(_snap())
    assert s.resolve_selector({"text": "Wi-Fi"}) == (250, 150)


def test_resolve_selector_raises_stale_when_absent():
    s = Session(); s.set_snapshot(_snap())
    with pytest.raises(errors.StaleSnapshotError):
        s.resolve_selector({"text": "Nonexistent"})


def test_find_without_snapshot_raises():
    with pytest.raises(KeyError):
        Session().find({"text": "x"})


def test_find_records_last_match_for_capture():
    # The resolved index is threaded into the daemon run-record so the selector-library can learn
    # selector -> matched_i. find() records the primary match; a miss clears it.
    s = Session(); s.set_snapshot(_snap())
    assert s.last_match is None
    s.find({"text": "Bluetooth"})
    assert s.last_match == 1
    s.find({"text": "Nonexistent"})
    assert s.last_match is None


def test_resolve_selector_sets_last_match():
    s = Session(); s.set_snapshot(_snap())
    s.resolve_selector({"text": "Wi-Fi"})
    assert s.last_match == 0
