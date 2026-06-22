"""DaemonServer: warm runtime + synchronous handle_line dispatch (single writer)."""
from __future__ import annotations

import json
import os
import threading
import time

from phonectl import audit, config, errors, observer, policy, results
from phonectl.daemon import PROTOCOL_VERSION
from phonectl.daemon import rpc as rpc_mod
from phonectl.daemon.discovery import LOOPBACK
from phonectl.daemon.events import EventBus
from phonectl.daemon.jobs import JobRegistry
from phonectl.daemon.snapshots import SnapshotCache


class DaemonServer:
    def __init__(self, cfg, *, build=None, now=time.time, registry=None) -> None:
        host = cfg.get("daemon_host", "127.0.0.1")
        if host not in LOOPBACK:
            raise ValueError(f"daemon is loopback-only; refusing daemon_host {host!r}")
        if build is None:
            from phonectl import cli
            build = cli.build_runtime
        self._cfg = cfg
        self._host = host
        self._build = build
        self._now = now
        self.registry = registry or rpc_mod.Registry()
        self._write_lock = threading.Lock()
        self._warm = None
        self._sock = None
        self._running = False
        self._port = None
        self.snapshots = SnapshotCache()
        self.events = EventBus()
        self.jobs = JobRegistry(
            self._run_job,
            queue_max=cfg.get("job_queue_max", 8),
            idempotency_ttl=cfg.get("idempotency_ttl", 300.0),
            now=now,
        )
        # poller is built lazily on first events_poll call (avoids eager triple build)
        self.poller = None
        self._register_builtins()

    # ── warm provider lifecycle ─────────────────────────────────────────────

    def _warm_triple(self):
        if self._warm is None:
            self._warm = self._build(self._cfg)
        return self._warm

    # ── built-in RPC handlers ───────────────────────────────────────────────

    def _register_builtins(self) -> None:
        @self.registry.register("ping")
        def _ping(params, ctx):
            return results.ok(capability="daemon.ping", data={"pong": True})

        @self.registry.register("act")
        def _act(params, ctx):
            p = dict(params)
            p["request_id"] = ctx.get("request_id")
            job_id = self.jobs.submit("act", p)
            return results.ok(
                capability="daemon.job_accepted",
                data={"job_id": job_id, "status": self.jobs.get(job_id).status
                      if self.jobs.get(job_id).status != "queued" else "accepted"},
            )

        @self.registry.register("observe")
        def _observe(params, ctx):
            p = dict(params)
            p["request_id"] = ctx.get("request_id")
            job_id = self.jobs.submit("observe", p)
            return results.ok(capability="daemon.job_accepted",
                              data={"job_id": job_id, "status": "accepted"})

        @self.registry.register("find")
        def _find(params, ctx):
            p = dict(params)
            p["request_id"] = ctx.get("request_id")
            job_id = self.jobs.submit("find", p)
            return results.ok(capability="daemon.job_accepted",
                              data={"job_id": job_id, "status": "accepted"})

        @self.registry.register("capabilities")
        def _capabilities(params, ctx):
            reg, _, _ = self._warm_triple()
            return results.ok(capability="daemon.capabilities", data=reg.capabilities())

        @self.registry.register("policy_explain")
        def _policy_explain(params, ctx):
            _, session, _ = self._warm_triple()
            decision = policy.explain(
                session.last, params["verb"], params.get("target", {}), self._cfg
            )
            return results.ok(capability="policy.explain", data=decision)

        @self.registry.register("audit_query")
        def _audit_query(params, ctx):
            entries = audit.read_entries(limit=params.get("limit"))
            return results.ok(capability="audit.query", data=entries)

        @self.registry.register("stop")
        def _stop(params, ctx):
            (config.config_dir() / "STOP").write_text("stopped via daemon\n")
            return results.ok(capability="daemon.stop", data={"stopped": True})

        @self.registry.register("resume")
        def _resume(params, ctx):
            p = config.config_dir() / "STOP"
            if p.exists():
                p.unlink()
            return results.ok(capability="daemon.resume", data={"stopped": False})

        @self.registry.register("status")
        def _status(params, ctx):
            return results.ok(capability="daemon.status", data={
                "pid": os.getpid(),
                "host": self._host,
                "port": self._port,
                "protocol_version": PROTOCOL_VERSION,
                "warm": self._warm is not None,
                "methods": sorted(self.registry._handlers),
            })

        @self.registry.register("job_poll")
        def _job_poll(params, ctx):
            job = self.jobs.get(params.get("job_id"))
            if job is None:
                return results.err(
                    ("unknown_job", f"no job {params.get('job_id')!r}"),
                    user_action="Submit the action again; the job id is unknown to this daemon.",
                )
            return results.ok(
                capability="daemon.job_poll",
                data={"status": job.status, "result": job.result_env},
            )

        @self.registry.register("events_poll")
        def _events_poll(params, ctx):
            # Build the poller lazily on first call
            if self.poller is None:
                from phonectl.daemon.poller import EventPoller
                pr, _, _ = self._warm_triple()
                ui_src = pr.for_capability("observe_ui_events") if hasattr(pr, "for_capability") else None
                notif_src = pr.for_capability("observe_notifications") if hasattr(pr, "for_capability") else None
                self.poller = EventPoller(self.events, ui_source=ui_src, notif_source=notif_src)
            self.poller.drain_once()
            since = int(params.get("since", 0))
            max_n = int(params.get("max", 100))
            return results.ok(
                capability="events.poll",
                data=self.events.poll(since, max=max_n),
                request_id=ctx.get("request_id"),
            )

    # ── worker run-fns (executed by the JobRegistry worker) ──────────────
    def _run_job(self, method, params):
        with self._write_lock:
            if method == "act":
                return self._run_act(params)
            if method == "observe":
                return self._run_observe(params)
            if method == "find":
                return self._run_find(params)
            return results.err(("internal_error", f"no run-fn for {method!r}"))

    def _run_act(self, params):
        from phonectl import runtime
        import uuid
        verb = params["verb"]
        target = params.get("target", {})
        request_id = params.get("request_id")

        try:
            self.snapshots.validate(
                params.get("snapshot_id"),
                current_foreground=self.snapshots.current_foreground,
            )
        except errors.StaleSnapshotError as exc:
            env = results.err(
                exc,
                user_action="Re-observe (the screen changed); resolve the index against a fresh snapshot.",
                request_id=request_id,
            )
            self.events.publish(
                "action_finished",
                {"verb": verb, "target": target, "request_id": request_id,
                 "ok": False, "snapshot_after": None},
                source="daemon",
            )
            return env

        self.events.publish(
            "action_started",
            {"verb": verb, "target": target, "request_id": request_id},
            source="daemon",
        )

        fn = self._fn_for(params)
        snapshot_before = self.snapshots.current_id

        def warm_build(cfg):
            return self._warm_triple()

        env = runtime.run_action(
            verb, fn, target,
            build=warm_build,
            yes=bool(params.get("yes", False)),
            cfg=self._cfg,
            request_id=request_id,
            idempotency_key=params.get("idempotency_key"),
        )

        snapshot_after = None
        _, session, _ = self._warm_triple()
        if env.get("ok") and session.last is not None:
            snapshot_after = self.snapshots.put(session.last)
            env["snapshot_before"] = snapshot_before
            env["snapshot_after"] = snapshot_after

        self.events.publish(
            "action_finished",
            {"verb": verb, "target": target, "request_id": request_id,
             "ok": bool(env.get("ok")), "snapshot_after": snapshot_after},
            source="daemon",
        )

        from phonectl.daemon import records as _records
        rec = _records.build_record(env, params, action_id=uuid.uuid4().hex, now=self._now)
        rec["snapshot_before"] = snapshot_before
        rec["snapshot_after"] = snapshot_after
        self._append_record(rec)
        return env

    def _run_observe(self, params):
        reg, session, conn = self._warm_triple()
        if hasattr(conn, "ensure"):
            conn.ensure()
        snap = observer.observe(reg, session, **{
            k: params[k] for k in ("screenshot", "snap_path", "tree", "relations")
            if k in params
        })
        snapshot_id = self.snapshots.put(snap)
        return results.ok(
            capability="ui.observe",
            provider=getattr(reg, "last_used", None) or "adb",
            data=snap,
            snapshot_id=snapshot_id,
        )

    def _run_find(self, params):
        reg, session, conn = self._warm_triple()
        if hasattr(conn, "ensure"):
            conn.ensure()
        observer.observe(reg, session)
        matches = session.find(params.get("selector", {}))
        return results.ok(capability="ui.find", data={"matches": matches})

    # ── core dispatch ───────────────────────────────────────────────────────

    def handle_line(self, line: str) -> str:
        try:
            req = json.loads(line)
            method = req["method"]
            params = req.get("params", {})
            rid = req.get("request_id")
        except (ValueError, KeyError, TypeError):
            return self._finish(
                results.err(("bad_request", "malformed RPC request line")), None
            )

        ctx = {"server": self, "request_id": rid}
        if method in rpc_mod.MUTATING:
            with self._write_lock:
                env = self.registry.dispatch(method, params, ctx)
        else:
            env = self.registry.dispatch(method, params, ctx)
        return self._finish(env, rid)

    def _finish(self, env: dict, rid) -> str:
        env = dict(env)
        env.setdefault("request_id", rid)
        env["version"] = PROTOCOL_VERSION
        return json.dumps(env)

    # ── run record helpers ──────────────────────────────────────────────────

    def _append_record(self, rec):
        from phonectl.daemon import records as _records
        _records.append(rec)

    # ── action fn mapping ───────────────────────────────────────────────────

    def _fn_for(self, params):
        from phonectl import actuator
        verb = params["verb"]
        target = params.get("target")
        if verb == "tap":
            if "i" in params:
                i = params["i"]
                return lambda b, s: actuator.tap(b, s, i=i)
            if isinstance(target, dict) and "i" in target:
                i = target["i"]
                return lambda b, s: actuator.tap(b, s, i=i)
            if isinstance(target, str) and target.startswith("i="):
                i = int(target.split("=", 1)[1])
                return lambda b, s: actuator.tap(b, s, i=i)
            x, y = params["x"], params["y"]
            return lambda b, s: actuator.tap(b, s, x=x, y=y)
        if verb == "type":
            text = params["text"]
            return lambda b, s: actuator.type_text(b, s, text)
        if verb == "swipe":
            coords = params["coords"]
            return lambda b, s: actuator.swipe(b, s, *coords)
        if verb == "key":
            key = params["key"]
            return lambda b, s: actuator.key(b, s, key)
        if verb == "launch":
            pkg = params["package"]
            return lambda b, s: actuator.launch(b, s, pkg)
        raise NotImplementedError(f"no fn mapping for verb {verb!r}")

    # ── lifecycle (bind / serve / shutdown) ─────────────────────────────────

    def _publish_lifecycle(self, phase: str) -> None:
        self.events.publish("lifecycle", {"phase": phase}, source="daemon")

    def bind(self, *, server_factory=None) -> tuple:
        if server_factory is None:
            import socket as _socket

            def server_factory(host):
                s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
                s.bind((host, 0))
                s.listen()
                return s

        from phonectl.daemon import discovery
        self._sock = server_factory(self._host)
        self._port = self._sock.getsockname()[1]
        discovery.write({
            "pid": os.getpid(),
            "host": self._host,
            "port": self._port,
            "version": PROTOCOL_VERSION,
            "started_at": self._now(),
        })
        self._publish_lifecycle("started")
        return self._host, self._port

    def serve_forever(self):
        import selectors
        self._running = True
        sel = selectors.DefaultSelector()
        sel.register(self._sock, selectors.EVENT_READ)
        try:
            while self._running:
                for key, _ in sel.select(timeout=0.5):
                    conn, _addr = key.fileobj.accept()
                    self._serve_conn(conn)
        finally:
            sel.close()

    def _serve_conn(self, conn):
        f = conn.makefile("rw", encoding="utf-8", newline="\n")
        try:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                f.write(self.handle_line(line) + "\n")
                f.flush()
        finally:
            f.close()
            conn.close()

    def shutdown(self):
        from phonectl.daemon import discovery
        self._publish_lifecycle("stopped")
        self._running = False
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        discovery.remove()
