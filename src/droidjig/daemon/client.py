"""DaemonClient: frontend RPC over the Plan-4.3 SocketTransport."""
from __future__ import annotations

import socket
import time

from droidjig import errors, results
from droidjig.daemon import PROTOCOL_VERSION
from droidjig.providers.transport import SocketTransport, next_request_id


class DaemonClient:
    def __init__(self, host, port, *, transport=None, version=PROTOCOL_VERSION,
                 token=None) -> None:
        self._host, self._port, self._version = host, port, version
        self._transport = transport or SocketTransport(host, port, version=version,
                                                       token=token)

    @classmethod
    def from_discovery(cls, info, *, transport=None):
        return cls(info["host"], info["port"], transport=transport,
                   version=info.get("version", PROTOCOL_VERSION),
                   token=info.get("token"))

    def call(self, method, params=None, *, timeout=5.0) -> dict:
        rid = next_request_id()
        try:
            resp = self._transport.request(method, params or {}, request_id=rid, timeout=timeout)
        except (socket.timeout, TimeoutError):
            return results.err(("timeout", f"daemon call {method!r} timed out"))
        except (ConnectionError, OSError):
            return results.err(errors.DaemonUnreachableError(f"daemon {method!r} unreachable"))
        if not isinstance(resp, dict) or resp.get("request_id") not in (rid, None):
            return results.err(errors.DaemonUnreachableError("no matching daemon response"))
        if resp.get("ok") is None and "error" not in resp:
            return results.err(errors.DaemonUnreachableError("malformed daemon response"))
        return resp

    def is_running(self) -> bool:
        return bool(self.call("ping", {}, timeout=1.0).get("ok"))

    def submit_and_wait(self, method, params=None, *, overall_timeout,
                        poll_interval=0.5, sleep=time.sleep, now=time.monotonic) -> dict:
        acc = self.call(method, params)
        if not acc.get("ok"):
            return acc                                   # unreachable / busy / timeout
        job_id = acc.get("data", {}).get("job_id")
        if job_id is None:
            return acc          # older daemon ran the method synchronously
        deadline = now() + overall_timeout
        # Ramped polling: start fast so short actions return promptly, then back
        # off to poll_interval so long jobs don't hammer the daemon.
        delay = min(0.05, poll_interval)
        while now() < deadline:
            polled = self.call("job_poll", {"job_id": job_id})
            if not polled.get("ok"):
                return polled
            data = polled["data"]
            if data["status"] in ("done", "error"):
                return data["result"]
            sleep(delay)
            delay = min(delay * 2, poll_interval)
        return results.err(
            errors.JobTimeoutError(
                f"job {job_id} still running after {overall_timeout}s"),
            user_action=f"The action is still running. Query it with: droidjig job {job_id}",
            job_id=job_id,
        )
