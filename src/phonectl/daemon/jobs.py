"""JobRegistry: bounded FIFO of device jobs + single worker (single-writer)."""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass

from phonectl import errors, results


@dataclass
class Job:
    job_id: str
    method: str
    params: dict
    status: str = "queued"          # queued | running | done | error
    result_env: dict | None = None
    idempotency_key: str | None = None
    ts_created: float = 0.0
    ts_started: float | None = None
    ts_finished: float | None = None


_TERMINAL = {"done", "error"}


class JobRegistry:
    def __init__(self, run_fn, *, queue_max=8, idempotency_ttl=300.0,
                 now=time.time, new_id=None) -> None:
        self._run_fn = run_fn
        self._queue_max = queue_max
        self._ttl = idempotency_ttl
        self._now = now
        self._new_id = new_id or (lambda: uuid.uuid4().hex)
        self._jobs: dict[str, Job] = {}
        self._by_key: dict[str, str] = {}      # idempotency_key -> job_id
        self._queue: deque[str] = deque()
        self._cv = threading.Condition()
        self._stopped = False
        self._worker: threading.Thread | None = None

    # ── submission / lookup ─────────────────────────────────────────────
    def submit(self, method: str, params: dict) -> str:
        key = params.get("idempotency_key")
        with self._cv:
            self._sweep_locked()
            existing = self._dedupe_locked(key)
            if existing is not None:
                return existing
            if len(self._queue) >= self._queue_max:
                raise errors.BusyError(
                    f"job queue full ({self._queue_max}); retry shortly")
            jid = self._new_id()
            self._jobs[jid] = Job(
                job_id=jid, method=method, params=params,
                idempotency_key=key, ts_created=self._now(),
            )
            if key is not None:
                self._by_key[key] = jid
            self._queue.append(jid)
            self._cv.notify_all()
            return jid

    def _dedupe_locked(self, key):
        if key is None:
            return None
        jid = self._by_key.get(key)
        if jid is None:
            return None
        job = self._jobs.get(jid)
        if job is None:
            return None
        if job.status not in _TERMINAL:
            return jid                                   # queued or running
        if job.ts_finished is not None and (self._now() - job.ts_finished) < self._ttl:
            return jid                                   # finished within ttl
        return None

    def _sweep_locked(self):
        cutoff = self._now() - self._ttl
        expired = [jid for jid, job in self._jobs.items()
                   if job.status in _TERMINAL
                   and job.ts_finished is not None
                   and job.ts_finished <= cutoff]
        for jid in expired:
            job = self._jobs.pop(jid, None)
            if (job is not None and job.idempotency_key is not None
                    and self._by_key.get(job.idempotency_key) == jid):
                del self._by_key[job.idempotency_key]

    def get(self, job_id: str) -> Job | None:
        with self._cv:
            return self._jobs.get(job_id)

    # ── execution ───────────────────────────────────────────────────────
    def run_next(self, block: bool = False, timeout: float | None = None) -> bool:
        with self._cv:
            while not self._queue:
                if not block or self._stopped:
                    return False
                self._cv.wait(timeout=timeout)
                if self._stopped:
                    return False
            jid = self._queue.popleft()
            job = self._jobs[jid]
            job.status = "running"
            job.ts_started = self._now()
        try:
            env = self._run_fn(job.method, job.params)
        except Exception as exc:  # noqa: BLE001 — never let the worker die
            env = results.err(("internal_error", str(exc)))
        with self._cv:
            job.result_env = env
            job.status = "done" if env.get("ok") else "error"
            job.ts_finished = self._now()
        return True

    # ── worker lifecycle ────────────────────────────────────────────────
    def start(self) -> None:
        with self._cv:
            if self._worker is not None:
                return
            self._stopped = False
            worker = threading.Thread(
                target=self._loop, name="phonectl-job-worker", daemon=True)
            self._worker = worker
        worker.start()

    def _loop(self) -> None:
        while not self._stopped:
            self.run_next(block=True, timeout=0.5)

    def stop(self) -> None:
        with self._cv:
            self._stopped = True
            self._cv.notify_all()
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.join(timeout=2.0)
