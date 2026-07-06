"""phonectl evaluation suite (Phase X).

A repeatable phone-agent benchmark that runs WITHOUT a device: a scripted fake-provider simulator
drives the real observe -> policy -> act -> audit pipeline (``runtime.run_action``) over crafted UI
screens, and the harness reports the strategy §26 metrics (success rate, median latency, action
count, stale-target rate, provider-fallback count, human interventions).

Layout:
  - ``simulator``  ScriptedBackend (implements the Backend protocol) + screen builders.
  - ``metrics``    pure envelope -> metrics derivation.
  - ``harness``    meters run_action, isolates PHONECTL_HOME.
  - ``scenarios``  the seven §26 benchmark scenarios.

Run standalone: ``python -m eval``. The pytest gate lives in ``tests/test_eval_suite.py``.
The real-device lane is a documented stub (see ``@pytest.mark.device``); it is never run in CI.
"""
