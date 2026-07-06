"""Standalone benchmark runner: ``python -m eval``.

Runs the seven §26 scenarios in an isolated PHONECTL_HOME and prints a per-scenario pass/fail line
plus the aggregate metrics. Exit code is non-zero if any scenario fails, so it can gate CI too.
"""
from __future__ import annotations

import sys

from eval import metrics
from eval.harness import isolated_home
from eval.scenarios import run_all


def main() -> int:
    with isolated_home():
        reports = run_all()

    rows = []
    all_results = []
    print(f"{'scenario':<24} {'pass':<6} {'actions':<8} {'success':<8} "
          f"{'p50 ms':<8} {'interventions'}")
    print("-" * 72)
    for r in reports:
        s = r["summary"]
        rows.append(r)
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"{r['name']:<24} {mark:<6} {s['action_count']:<8} "
              f"{s['success_rate']:<8.2f} {s['median_latency_ms']:<8.1f} "
              f"{s['human_interventions']}")

    passed = sum(1 for r in rows if r["passed"])
    print("-" * 72)
    print(f"{passed}/{len(rows)} scenarios passed")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
