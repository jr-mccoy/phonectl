"""CI gate for the Phase X eval suite (eval/).

Runs the seven §26 scenarios against the device-free simulator and asserts each passes, plus the
metric derivations. The real-device lane is a documented, skipped stub (@pytest.mark.device).
"""
import pytest

from eval import metrics
from eval.scenarios import (
    SCENARIOS, messaging_dry_run, recovery_drill, safety_drill,
)

METRIC_KEYS = {
    "action_count", "success_rate", "median_latency_ms",
    "stale_target_rate", "provider_fallback_count", "human_interventions",
}


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DROIDJIG_HOME", str(tmp_path))


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.__name__)
def test_scenario_passes(scenario):
    r = scenario()
    assert r["passed"], f"{r['name']} failed (detail={r.get('detail')!r})"
    assert METRIC_KEYS <= set(r["summary"])
    assert r["summary"]["action_count"] >= 1


def test_messaging_dry_run_counts_human_intervention():
    # The confirm gate hands off to a human: one intervention, zero successes, nothing executed.
    r = messaging_dry_run()
    assert r["passed"]
    assert r["summary"]["human_interventions"] == 1
    assert r["summary"]["success_rate"] == 0.0


def test_safety_drill_is_denied():
    r = safety_drill()
    assert r["passed"] and r["summary"]["success_rate"] == 0.0


def test_recovery_drill_yields_structured_error():
    r = recovery_drill()
    assert r["passed"] and r["detail"] in ("device_locked", "observe_failed")


def test_metrics_summarize_empty():
    s = metrics.summarize([])
    assert s == {
        "action_count": 0, "success_rate": 0.0, "median_latency_ms": 0.0,
        "stale_target_rate": 0.0, "provider_fallback_count": 0, "human_interventions": 0,
    }


def test_metrics_summarize_mixed():
    rows = [
        {"ok": True, "latency_ms": 10.0, "stale": False, "fallback": False,
         "intervention": False, "error_code": None, "provider": "x"},
        {"ok": False, "latency_ms": 30.0, "stale": True, "fallback": True,
         "intervention": False, "error_code": "stale_snapshot", "provider": "y"},
    ]
    s = metrics.summarize(rows)
    assert s["action_count"] == 2
    assert s["success_rate"] == 0.5
    assert s["median_latency_ms"] == 20.0
    assert s["stale_target_rate"] == 0.5
    assert s["provider_fallback_count"] == 1


def test_action_result_flags_confirmation_as_intervention():
    env = {"ok": False, "error": {"code": "confirmation_required"}}
    row = metrics.action_result(env, 1.0)
    assert row["intervention"] is True and row["ok"] is False


@pytest.mark.device
def test_real_device_lane_stub():
    # Placeholder for the real-device benchmark lane (strategy §27#7). Runs the same scenarios
    # against a live device over Wireless Debugging; gated behind `-m device` and never run in CI.
    pytest.skip("real-device lane: needs hardware over Wireless Debugging (never run in CI)")
