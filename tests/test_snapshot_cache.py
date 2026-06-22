import itertools

import pytest

from phonectl.daemon.snapshots import SnapshotCache


def _snap(pkg="com.android.settings", h="abc"):
    return {"app": {"package": pkg, "activity": ".Main"}, "hash": h, "elements": []}


def test_put_mints_monotonic_ids():
    cache = SnapshotCache()
    a = cache.put(_snap())
    b = cache.put(_snap())
    assert a == "snap_1"
    assert b == "snap_2"
    assert cache.current_id == "snap_2"


def test_get_returns_cached_snapshot_and_none_for_unknown():
    cache = SnapshotCache()
    sid = cache.put(_snap(h="xyz"))
    assert cache.get(sid)["hash"] == "xyz"
    assert cache.get("snap_999") is None


def test_current_id_is_none_before_first_put():
    cache = SnapshotCache()
    assert cache.current_id is None
    assert cache.current_foreground is None


def test_foreground_accessors_read_app_package():
    cache = SnapshotCache()
    sid = cache.put(_snap(pkg="com.bank.app"))
    assert cache.current_foreground == "com.bank.app"
    assert cache.foreground_of(sid) == "com.bank.app"
    assert cache.foreground_of("snap_999") is None


def test_injectable_id_counter_is_used():
    cache = SnapshotCache(id_counter=itertools.count(100))
    assert cache.put(_snap()) == "snap_100"


# Task 3: validate
from phonectl import errors


def test_validate_passes_when_expected_matches_current():
    cache = SnapshotCache()
    sid = cache.put(_snap(pkg="com.x"))
    cache.validate(sid, current_foreground="com.x")  # no raise


def test_validate_raises_when_expected_is_stale():
    cache = SnapshotCache()
    cache.put(_snap())            # snap_1
    cache.put(_snap())            # snap_2 (current)
    with pytest.raises(errors.StaleSnapshotError):
        cache.validate("snap_1", current_foreground="com.android.settings")


def test_validate_raises_when_foreground_changed():
    cache = SnapshotCache()
    sid = cache.put(_snap(pkg="com.x"))
    with pytest.raises(errors.StaleSnapshotError):
        cache.validate(sid, current_foreground="com.bank.app")


def test_validate_none_expected_is_noop():
    cache = SnapshotCache()
    cache.put(_snap())
    cache.validate(None, current_foreground="anything")  # no raise
