"""DaemonServer: warm runtime + synchronous handle_line dispatch (single writer)."""
from __future__ import annotations

import json
import os
import threading
import time

from phonectl import audit, errors, observer, policy, results, trust
from phonectl.daemon import PROTOCOL_VERSION
from phonectl.daemon import rpc as rpc_mod
from phonectl.daemon.discovery import LOOPBACK
from phonectl.daemon.events import EventBus
from phonectl.daemon.jobs import JobRegistry
from phonectl.daemon.snapshots import SnapshotCache


class DaemonServer:
    def __init__(self, cfg, *, build=None, now=time.time, registry=None,
                 app_version=None, locale=None) -> None:
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
        # Context resolvers for the memory selector-library key (package|app_version|locale).
        # Kept injectable and defaulting to "?" so capture never costs an extra round trip and
        # never blocks on the companion; full app_version/locale capture is deferred (roadmap §9).
        self._app_version = app_version or (lambda package: "?")
        self._locale = locale or (lambda: "?")
        self.registry = registry or rpc_mod.Registry()
        self._write_lock = threading.Lock()
        self._warm = None
        self._sock = None
        self._running = False
        self._port = None
        # Shared-secret token required on every RPC once the daemon is network-exposed
        # (set in bind()). Loopback is not a UID boundary on Android — see Finding 2.
        self._token = None
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
        self._macro_tokens = {}
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
            return results.ok(capability="daemon.job_accepted",
                              data={"job_id": job_id, "status": "accepted"})

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

        @self.registry.register("shutdown")
        def _shutdown(params, ctx):
            self._running = False
            return results.ok(capability="daemon.shutdown", data={"stopping": True})

        @self.registry.register("stop")
        def _stop(params, ctx):
            audit.engage_stop("stopped via daemon\n")
            return results.ok(capability="daemon.stop", data={"stopped": True})

        # No "resume" RPC: clearing the kill switch is a human-only, out-of-band
        # action (Finding 1). Resume via `phonectl resume` on the host or the
        # companion notification/tile — never through an agent-reachable RPC.

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

        from phonectl.macro import schema as _mschema

        @self.registry.register("macro_validate")
        def _macro_validate(params, ctx):
            errs = _mschema.validate(params["macro"])
            return results.ok(capability="macro.validate",
                              data={"valid": not errs, "errors": errs})

        @self.registry.register("macro_run")
        def _macro_run(params, ctx):
            # Async job, like act/observe: a multi-step macro runs for minutes,
            # far past any client RPC deadline, and must hold the single-writer
            # lock while it drives the device (Finding 2, 2026-07-04).
            job_id = self.jobs.submit("macro_run", dict(params))
            return results.ok(capability="daemon.job_accepted",
                              data={"job_id": job_id, "status": "accepted"})

        @self.registry.register("macro_cancel")
        def _macro_cancel(params, ctx):
            tok = self._macro_tokens.get(params.get("run_id"))
            if tok is not None:
                tok.cancel()
            return results.ok(capability="macro.cancel", data={"cancelled": tok is not None})

        @self.registry.register("macro_status")
        def _macro_status(params, ctx):
            from phonectl.macro import records as _records
            return results.ok(capability="macro.status",
                              data={"runs": _records.read(kind="macro_run",
                                                          limit=params.get("limit", 10))})

        from phonectl.macro import registry as _mreg

        @self.registry.register("macro_enable")
        def _macro_enable(params, ctx):
            _mreg.enable(params["macro"])
            return results.ok(capability="macro.enable", data={"enabled": True})

        @self.registry.register("macro_disable")
        def _macro_disable(params, ctx):
            _mreg.disable(params["name"])
            return results.ok(capability="macro.disable", data={"enabled": False})

        @self.registry.register("macro_list")
        def _macro_list(params, ctx):
            return results.ok(capability="macro.list", data={"macros": _mreg.all()})

        from phonectl.macro import autonomy as _aut
        from phonectl.macro import memory as _mem
        import time as _t

        @self.registry.register("autonomy_grant")
        def _autonomy_grant(params, ctx):
            g = _aut.grant(params["macro"], max_risk=params["max_risk"],
                           scope=params.get("scope", "all"),
                           expires_at=params.get("expires_at"), now=_t.time())
            return results.ok(capability="autonomy.grant", data=g)

        @self.registry.register("autonomy_revoke")
        def _autonomy_revoke(params, ctx):
            _aut.revoke(macro=params.get("macro"), grant_id=params.get("grant_id"), now=_t.time())
            return results.ok(capability="autonomy.revoke", data={"revoked": True})

        @self.registry.register("autonomy_list")
        def _autonomy_list(params, ctx):
            return results.ok(capability="autonomy.list", data={"grants": _aut.list_live(now=_t.time())})

        @self.registry.register("memory_show")
        def _memory_show(params, ctx):
            store = params.get("store")
            return results.ok(capability="memory.show",
                              data=_mem.read(store) if store else _mem.export())

        @self.registry.register("memory_export")
        def _memory_export(params, ctx):
            return results.ok(capability="memory.export", data=_mem.export())

        @self.registry.register("memory_delete")
        def _memory_delete(params, ctx):
            _mem.delete(params.get("store"))
            return results.ok(capability="memory.delete", data={"deleted": True})

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
            try:
                if getattr(self, "_trigger_mgr", None) is None:
                    from phonectl.daemon.triggers import TriggerManager, Scheduler
                    from phonectl.macro.engine import Engine as _Eng
                    eng = _Eng(build=lambda cfg: self._warm_triple(), cfg=self._cfg)
                    self._trigger_mgr = TriggerManager(
                        eng, poll=lambda since, mx: self.events.poll(since, max=mx))
                    self._scheduler = Scheduler(eng)
                self._trigger_mgr.step()
                self._scheduler.due()
            except Exception:
                pass
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
            if method == "macro_run":
                return self._run_macro(params)
            return results.err(("internal_error", f"no run-fn for {method!r}"))

    def _run_macro(self, params):
        from phonectl.macro import schema as _mschema
        from phonectl.macro.engine import Engine, CancellationToken
        macro = _mschema.parse(params["macro"])
        token = CancellationToken()
        eng = Engine(build=lambda cfg: self._warm_triple(), cfg=self._cfg)
        env = eng.run(macro, token=token, yes=bool(params.get("yes", False)))
        rid = env.get("data", {}).get("run_id")
        if rid:
            self._macro_tokens.pop(rid, None)
        return env

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
        rec = _records.build_record(
            env, params, action_id=uuid.uuid4().hex, now=self._now,
            matched_i=getattr(session, "last_match", None),
            context=self._capture_context(session),
        )
        rec["snapshot_before"] = snapshot_before
        rec["snapshot_after"] = snapshot_after
        self._append_record(rec)
        # Feed the user-controlled memory selector-library. Best-effort: a capture failure must
        # never fail the action it describes.
        try:
            from phonectl.macro import memory as _memory
            _memory.capture_from_runs([rec])
        except Exception:
            pass
        return env

    def _capture_context(self, session):
        """The {package, app_version, locale} key for the memory selector-library."""
        app = (getattr(session, "last", None) or {}).get("app") or {}
        package = app.get("package") or "?"
        return {
            "package": package,
            "app_version": self._app_version(package),
            "locale": self._locale(),
        }

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

        # Token gate (Finding 2): once bound to a socket the daemon holds a shared
        # secret and every method except the liveness probe must present it. `ping`
        # stays open so discovery can detect the daemon without the token.
        if self._token is not None and method != "ping":
            if not trust.tokens_equal(req.get("token"), self._token):
                return self._finish(
                    results.err(
                        errors.UnauthorizedError("missing or invalid daemon token"),
                        user_action="Use the phonectl CLI/MCP on this host; other "
                                    "apps cannot read the daemon token.",
                    ),
                    rid,
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
        from phonectl import actuator, audit
        verb = params["verb"]
        target = params.get("target")
        tp = target if isinstance(target, dict) else {}

        def g(key, default=None):
            """Read a param from the top-level dict, falling back to the target dict.

            CLI dispatch merges dict-target keys up into params (_act_params), but
            direct RPC callers may nest everything under `target`; accept both."""
            if key in params:
                return params[key]
            return tp.get(key, default)

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
            if isinstance(target, dict) and "selector" in target:
                sel = target["selector"]
                return lambda b, s: actuator.tap(b, s, selector=sel)
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
        if verb == "named_swipe":
            direction = g("direction")
            distance_pct = g("distance_pct", 0.5)
            ms = g("ms", 400)
            within_i = g("within_i")
            return lambda b, s: actuator.named_swipe(
                b, s, direction, distance_pct=distance_pct, ms=ms, within_i=within_i)
        if verb == "scroll":
            direction = g("direction")
            within_i = g("within_i")
            distance_pct = g("distance_pct", 0.5)
            ms = g("ms", 400)
            return lambda b, s: actuator.scroll(
                b, s, direction, within_i=within_i, distance_pct=distance_pct, ms=ms)
        if verb == "long_press":
            i, x, y = g("i"), g("x"), g("y")
            sel = g("selector")
            duration_ms = g("duration_ms", 1000)
            return lambda b, s: actuator.long_press(
                b, s, i=i, x=x, y=y, selector=sel, duration_ms=duration_ms)
        if verb == "double_tap":
            i, x, y = g("i"), g("x"), g("y")
            sel = g("selector")
            interval_ms = g("interval_ms", 100)
            return lambda b, s: actuator.double_tap(
                b, s, i=i, x=x, y=y, selector=sel, interval_ms=interval_ms)
        if verb == "drag":
            coords = g("coords")
            duration_ms = g("duration_ms", 500)
            return lambda b, s: actuator.drag(b, s, *coords, duration_ms=duration_ms)
        if verb == "fling":
            direction = g("direction")
            return lambda b, s: actuator.fling(b, s, direction)
        if verb == "scroll_until":
            direction = g("direction", "down")
            text = g("text")
            sel = g("selector")
            max_scrolls = g("max_scrolls", 10)
            within_i = g("within_i")
            return lambda b, s: actuator.scroll_until(
                b, s, direction, text=text, selector=sel, max_scrolls=max_scrolls,
                within_i=within_i, halt=audit.kill_switch_active)
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
        self._token = discovery.new_token()
        discovery.write({
            "pid": os.getpid(),
            "host": self._host,
            "port": self._port,
            "version": PROTOCOL_VERSION,
            "token": self._token,
            "started_at": self._now(),
        })
        self._publish_lifecycle("started")
        return self._host, self._port

    def serve_forever(self):
        import selectors
        self._running = True
        self.jobs.start()
        sel = selectors.DefaultSelector()
        sel.register(self._sock, selectors.EVENT_READ)
        try:
            while self._running:
                for key, _ in sel.select(timeout=0.5):
                    conn, _addr = key.fileobj.accept()
                    self._serve_conn(conn)
        finally:
            sel.close()
            self.shutdown()

    # Upper bound on a single request line. A well-formed RPC (even a long macro or a base64
    # screenshot request) sits far below this; the cap stops a peer from pinning unbounded memory
    # with one never-terminated line (loopback is not a trust boundary on Android — Finding 2).
    MAX_LINE = 1 << 20  # 1 MiB

    def _serve_conn(self, conn):
        f = conn.makefile("rw", encoding="utf-8", newline="\n")
        try:
            while True:
                # readline(limit) reads at most limit+1 chars; a longer logical line comes back
                # without its trailing newline, which is how we detect and refuse an oversized line
                # instead of accumulating it.
                line = f.readline(self.MAX_LINE + 1)
                if not line:
                    break
                if len(line) > self.MAX_LINE and not line.endswith("\n"):
                    f.write(self._finish(
                        results.err(("request_too_large",
                                     f"request line exceeds {self.MAX_LINE} bytes")), None) + "\n")
                    f.flush()
                    break  # position is mid-line; the rest is untrusted, so drop the connection
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
        if not getattr(self, "_shutdown_done", False):
            self._publish_lifecycle("stopped")
        self._shutdown_done = True
        self._running = False
        self.jobs.stop()
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        discovery.remove()
