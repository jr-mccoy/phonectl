"""Pure derivation of the strategy §26 benchmark metrics from run_action envelopes.

Everything here operates on the structured ``results.ok/err`` envelope — never stdout — so the
metrics are stable across CLI/MCP/daemon frontends.
"""
from __future__ import annotations

from statistics import median


def action_result(env: dict, latency_ms: float) -> dict:
    """Normalize one run_action envelope + measured latency into a metric row."""
    ok = bool(env.get("ok"))
    code = None if ok else (env.get("error") or {}).get("code")
    return {
        "ok": ok,
        "latency_ms": latency_ms,
        "error_code": code,
        "provider": env.get("provider"),
        # provider_fallback is set by run_action when the registry walked down the stack.
        "fallback": bool(env.get("provider_fallback")),
        "stale": code == "stale_snapshot",
        # A confirm gate that the caller did not satisfy is a human-in-the-loop hand-off.
        "intervention": code == "confirmation_required",
    }


def summarize(results: list) -> dict:
    """Aggregate metric rows into the §26 report."""
    n = len(results)
    if n == 0:
        return {
            "action_count": 0, "success_rate": 0.0, "median_latency_ms": 0.0,
            "stale_target_rate": 0.0, "provider_fallback_count": 0, "human_interventions": 0,
        }
    return {
        "action_count": n,
        "success_rate": sum(r["ok"] for r in results) / n,
        "median_latency_ms": median(r["latency_ms"] for r in results),
        "stale_target_rate": sum(r["stale"] for r in results) / n,
        "provider_fallback_count": sum(1 for r in results if r["fallback"]),
        "human_interventions": sum(1 for r in results if r["intervention"]),
    }
