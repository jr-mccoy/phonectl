"""Pure bucketed sliding-window rate limiting helpers."""
from __future__ import annotations


def buckets_for(verb, level) -> list[str]:
    buckets = ["global", verb]
    if level in ("high", "critical"):
        buckets.append("high_risk")
    return buckets


def prune(history, now, window=60.0) -> list[dict]:
    return [record for record in history if now - record["ts"] <= window]


def check(history, verb, level, limits, now, window=60.0):
    recent = prune(history, now, window)
    for bucket in buckets_for(verb, level):
        limit = limits.get(bucket)
        if limit is None:
            continue
        count = sum(1 for record in recent if record["bucket"] == bucket)
        if count >= limit:
            return False, bucket
    return True, None


def repeated_hash(history_hashes, threshold=3) -> bool:
    if len(history_hashes) < threshold:
        return False
    return len(set(history_hashes[-threshold:])) == 1
