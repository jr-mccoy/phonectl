"""Pure risk policy decisions and agent-readable explanations."""
from __future__ import annotations

from phonectl import risk

DEFAULT_POLICY = {
    "low": "allow",
    "medium": "allow",
    "high": "confirm",
    "critical": "deny",
}


def decide(level, risk_policy=None) -> str:
    policy = {**DEFAULT_POLICY, **(risk_policy or {})}
    return policy.get(level, "confirm")


def explain(snapshot, verb, target, cfg) -> dict:
    classified = risk.classify(
        snapshot,
        verb,
        target,
        guarded_packages=cfg.get("guarded_packages", []),
    )
    decision = decide(classified["level"], cfg.get("risk_policy"))
    recommended_action = {
        "allow": "allowed",
        "confirm": "re-run with --yes to confirm",
        "deny": "blocked by policy; override risk_policy to permit",
    }[decision]
    return {
        "risk_level": classified["level"],
        "reasons": classified["reasons"],
        "decision": decision,
        "recommended_action": recommended_action,
    }
