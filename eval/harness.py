"""The eval harness: drive run_action, meter it, isolate state.

A ``Harness`` runs actions through the real ``runtime.run_action`` choke-point against a
``ScriptedBackend`` build, records a metric row per action, and reports the §26 summary. Scenarios
use it so their metrics come from the same pipeline the CLI/MCP/daemon use.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
import time

from phonectl import config, runtime
from eval import metrics


@contextlib.contextmanager
def isolated_home():
    """A fresh PHONECTL_HOME for a standalone run so benchmarks never touch real state."""
    prev = os.environ.get("PHONECTL_HOME")
    with tempfile.TemporaryDirectory(prefix="phonectl-eval-") as d:
        os.environ["PHONECTL_HOME"] = d
        try:
            yield d
        finally:
            if prev is None:
                os.environ.pop("PHONECTL_HOME", None)
            else:
                os.environ["PHONECTL_HOME"] = prev


def scripted_build(backend, session=None):
    """A run_action ``build`` returning the scripted trio (backend, Session, no-op Conn).

    The returned build carries a ``.session`` attribute so scenarios can inspect ``session.last``
    (the post-action snapshot) after driving an action.
    """
    from phonectl.session import Session

    class _Conn:
        def ensure(self):
            return None

    session = session or Session()

    def build(cfg):
        return backend, session, _Conn()

    build.session = session
    return build


class Harness:
    """Meters each run_action call and accumulates §26 metric rows."""

    def __init__(self, *, clock=time.perf_counter):
        self._clock = clock
        self.results: list = []

    def run(self, verb, fn, target, *, build, cfg=None, yes=False):
        cfg = config.load() if cfg is None else cfg
        t0 = self._clock()
        env = runtime.run_action(verb, fn, target, build=build, yes=yes, cfg=cfg)
        latency_ms = (self._clock() - t0) * 1000.0
        self.results.append(metrics.action_result(env, latency_ms))
        return env

    def summary(self) -> dict:
        return metrics.summarize(self.results)
