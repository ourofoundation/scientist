"""Local live view of the search, and the channel for researcher direction.

``scientist --watch`` serves one page that reads the memory database a run is
writing to. The page never talks to the run directly: it shows the events the
harness records, and anything the researcher asks for is stored as a directive
that the harness picks up at its next step boundary.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .computational.catalog import CATALOG
from .core.config import ScientistConfig
from .core.objective import ObjectiveSpec, compile_objective
from .core.search import campaign_key
from .core.store import Store
from .utils.logging import get_logger

logger = get_logger("watch")

PAGE = Path(__file__).with_name("watch.html")
DIRECTIVE_KINDS = {"steer", "pause", "choose", "queue", "stop", "grant"}
RECENT_EVENTS = 400


MAX_STEPS = 50
MAX_EXPENSIVE = 20


def run_command(
    config: ScientistConfig, goal: str, key: str, steps: int, expensive: int
) -> list:
    """Command line that runs ``steps`` of a campaign in this watcher's world."""
    command = [
        sys.executable, "-m", "scientist.main", goal,
        "--campaign", key, "--steps", str(steps), "--expensive", str(expensive),
    ]  # fmt: skip
    if config.sim:
        command += ["--sim", "--seed", str(config.sim_seed)]
    return command


class Watcher:
    """Reads the store for the page, writes the researcher's directives, and
    starts runs the researcher asks for."""

    def __init__(self, store: Store, config: Optional[ScientistConfig] = None) -> None:
        self.store = store
        self.config = config or ScientistConfig()
        self.lock = threading.Lock()  # one connection, many request threads
        # Runs this watcher started: campaign id → (process, log path).
        self.children: Dict[int, Tuple[Any, str]] = {}

    # -- goals and runs -------------------------------------------------------

    def info(self) -> Dict[str, Any]:
        cfg = self.config
        return {
            "sim": cfg.sim,
            "seed": cfg.sim_seed,
            "model": cfg.llm_model,
            "db_path": cfg.db_path,
            "can_compile": bool(cfg.llm_api_key),
            "can_run": bool(cfg.llm_api_key)
            and (cfg.sim or bool(cfg.ouro_api_key and cfg.ouro_team_id)),
            "defaults": {
                "steps": cfg.max_steps,
                "expensive": cfg.max_expensive_evals,
                "expensive_per_step": max(1, cfg.max_expensive_evals_per_step),
            },
            "catalog": [
                {
                    "name": p.name,
                    "description": p.description,
                    "unit": p.unit,
                    "tier": p.tier,
                    "boolean": p.boolean,
                }
                for p in CATALOG.values()
            ],
        }

    def compile(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """Turn a plain-language goal into an objective for the researcher to
        review. Nothing is stored until they start the run."""
        goal = str(body.get("goal") or "").strip()
        if len(goal) < 10:
            return 400, {"error": "describe the goal in a sentence or two"}
        if not self.config.llm_api_key:
            return 400, {"error": "OPENROUTER_API_KEY is not set for the watcher"}
        from .core.llm import LLM

        key = self._key(goal)
        with self.lock:
            existing = self.store.get_campaign(key)
        try:
            spec = compile_objective(LLM(self.config), goal)
        except Exception as e:
            return 502, {"error": f"could not compile the goal: {e}"}
        return 200, {
            "objective": spec.model_dump(),
            "existing_campaign": existing and existing["id"],
        }

    def create_campaign(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        goal = str(body.get("goal") or "").strip()
        if len(goal) < 10:
            return 400, {"error": "describe the goal in a sentence or two"}
        try:
            spec = ObjectiveSpec.model_validate(body.get("objective"))
        except Exception as e:
            return 400, {"error": f"objective is not valid: {e}"}
        spec.criteria = [c for c in spec.criteria if c.property in CATALOG]
        for c in spec.criteria:
            if c.direction == "require_true":
                c.target = None
            elif not c.target or c.target <= 0:
                return 400, {"error": f"{c.property} needs a target above zero"}
            if c.weight <= 0:
                return 400, {"error": f"{c.property} needs a weight above zero"}
        if not spec.criteria:
            return 400, {"error": "an objective needs at least one criterion"}
        if len({c.property for c in spec.criteria}) < len(spec.criteria):
            return 400, {"error": "each property can have only one criterion"}
        if set(spec.required_elements) & set(spec.forbidden_elements):
            return 400, {"error": "an element cannot be both required and forbidden"}

        key = self._key(goal)
        with self.lock:
            if self.store.get_campaign(key) is not None:
                return 409, {"error": "a campaign for this goal already exists"}
            campaign = self.store.create_campaign(key, goal)
            self.store.update_campaign(
                campaign["id"],
                objective=spec.model_dump(),
                temperature=self.config.t_initial,
            )
        status, started = self.start_run({**body, "campaign_id": campaign["id"]})
        return status, {**started, "campaign_id": campaign["id"]}

    def start_run(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        try:
            steps = int(body.get("steps") or self.config.max_steps)
            expensive = int(
                self.config.max_expensive_evals
                if body.get("expensive") is None
                else body["expensive"]
            )
        except (TypeError, ValueError):
            return 400, {"error": "steps and expensive must be whole numbers"}
        if not 1 <= steps <= MAX_STEPS or not 0 <= expensive <= MAX_EXPENSIVE:
            return 400, {
                "error": f"steps must be 1-{MAX_STEPS} and expensive "
                f"calculations 0-{MAX_EXPENSIVE}"
            }
        with self.lock:
            campaign = self.store.campaign(body.get("campaign_id"))
        if campaign is None or not campaign["objective"]:
            return 404, {"error": "no such campaign"}
        if self.runner(campaign["id"])["running"]:
            return 409, {"error": "a run is already in progress on this campaign"}
        if not self.info()["can_run"]:
            return 400, {"error": "the watcher is missing API keys needed to run"}
        log_path = self._spawn(campaign, steps, expensive)
        return 200, {"ok": True, "log": log_path}

    def _key(self, goal: str) -> str:
        key = campaign_key(goal)
        return f"sim-{key}" if self.config.sim else key

    def _spawn(self, campaign: Dict[str, Any], steps: int, expensive: int) -> str:
        """Start a run as its own process, so it outlives this watcher."""
        logs = os.path.join(os.path.dirname(os.path.abspath(self.config.db_path)), "logs")
        os.makedirs(logs, exist_ok=True)
        log_path = os.path.join(
            logs, f"{campaign['key']}-{time.strftime('%Y%m%d-%H%M%S')}.log"
        )
        command = run_command(
            self.config, campaign["goal"], campaign["key"], steps, expensive
        )
        with open(log_path, "w") as log:
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env={**os.environ, "SCIENTIST_DB": self.config.db_path},
                start_new_session=True,
            )
        self.children[campaign["id"]] = (process, log_path)
        logger.info(f"Started run for '{campaign['key']}' (pid {process.pid})")
        return log_path

    def runner(self, campaign_id: int) -> Dict[str, Any]:
        """State of the run this watcher started for a campaign, if any."""
        child = self.children.get(campaign_id)
        if child is None:
            return {"running": False, "started_here": False}
        process, log_path = child
        code = process.poll()
        out: Dict[str, Any] = {
            "running": code is None,
            "started_here": True,
            "exit_code": code,
            "log": log_path,
        }
        if code not in (None, 0):
            try:
                with open(log_path) as log:
                    out["log_tail"] = "".join(log.readlines()[-12:])
            except OSError:
                pass
        return out

    # -- reads --------------------------------------------------------------

    def campaigns(self) -> Any:
        with self.lock:
            rows = self.store.list_campaigns()
        out = []
        for c in rows:
            title = (
                ObjectiveSpec.model_validate(c["objective"]).title
                if c["objective"]
                else c["goal"][:80]
            )
            out.append(
                {
                    "id": c["id"],
                    "key": c["key"],
                    "title": title,
                    "steps_done": c["steps_done"],
                    "best_score": c["best_score"],
                }
            )
        return out

    def state(self, campaign_id: int) -> Optional[Dict[str, Any]]:
        with self.lock:
            campaign = self.store.campaign(campaign_id)
            if campaign is None:
                return None
            nodes = self.store.nodes(campaign_id)
            # Steps taken before `first_score` was stored: the step's own
            # event still says what it reached at the time.
            reached = {
                e["node_id"]: (e["data"] or {}).get("score")
                for e in self.store._rows(
                    "SELECT node_id, data FROM events WHERE campaign_id = ? "
                    "AND kind = 'step_done'",
                    (campaign_id,),
                )
            }
            for node in nodes:
                if node["first_score"] is None:
                    node["first_score"] = reached.get(node["id"])
            candidates = self.store.candidates(campaign_id)
            learnings = self.store.learnings(
                campaign_id, limit=200, include_retracted=True
            )
            directives = self.store.directives(campaign_id, status=None)[-60:]
            runs = self.store.runs(campaign_id)
            costs = self.store._row(
                "SELECT (SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls "
                "WHERE campaign_id = :c) AS llm_usd, "
                "(SELECT COUNT(*) FROM llm_calls WHERE campaign_id = :c) AS llm_calls, "
                "(SELECT COUNT(*) FROM route_calls WHERE campaign_id = :c) AS route_calls, "
                "(SELECT COALESCE(SUM(seconds), 0) FROM route_calls "
                "WHERE campaign_id = :c) AS route_seconds",
                {"c": campaign_id},
            )
            last = self.store._row(
                "SELECT COALESCE(MAX(id), 0) AS id FROM events WHERE campaign_id = ?",
                (campaign_id,),
            )["id"]

        objective = None
        if campaign["objective"]:
            spec = ObjectiveSpec.model_validate(campaign["objective"])
            objective = {
                **spec.model_dump(),
                "criteria": [
                    {
                        **c.model_dump(),
                        "unit": CATALOG[c.property].unit,
                        "tier": CATALOG[c.property].tier,
                    }
                    for c in spec.criteria
                    if c.property in CATALOG
                ],
            }
            for c in candidates:
                c["complete"] = spec.complete(c["properties"])
                c["ceiling"] = spec.ceiling(c["properties"])
                c["missing"] = [
                    p
                    for p in spec.properties()
                    if c["properties"].get(p) is None and CATALOG[p].route
                ]
        for c in candidates:
            for heavy in ("cif", "artifacts"):
                c.pop(heavy, None)
            c["errors"] = list(c["errors"])

        campaign.pop("objective", None)
        return {
            "campaign": campaign,
            "objective": objective,
            "nodes": nodes,
            "candidates": candidates[:200],
            "learnings": learnings,
            "directives": directives,
            "runs": runs,
            "costs": costs,
            "last_event_id": last,
            "runner": self.runner(campaign_id),
        }

    def events(self, campaign_id: int, after: Optional[int]) -> Any:
        with self.lock:
            if after is None:
                # First load: the tail is enough to rebuild the step in progress.
                last = self.store._row(
                    "SELECT COALESCE(MAX(id), 0) AS id FROM events "
                    "WHERE campaign_id = ?",
                    (campaign_id,),
                )["id"]
                after = max(0, last - RECENT_EVENTS)
            return self.store.events(campaign_id, after=after)

    # -- writes -------------------------------------------------------------

    def add_directive(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        kind = body.get("kind")
        campaign_id = body.get("campaign_id")
        payload = body.get("payload") or {}
        if kind not in DIRECTIVE_KINDS or not isinstance(campaign_id, int):
            return 400, {"error": "unknown directive kind or campaign"}
        if not isinstance(payload, dict):
            return 400, {"error": "payload must be an object"}
        if kind == "steer" and not str(payload.get("text", "")).strip():
            return 400, {"error": "a steering note needs text"}
        with self.lock:
            if self.store.campaign(campaign_id) is None:
                return 404, {"error": "no such campaign"}
            if kind == "pause" and any(
                d["kind"] == "pause" for d in self.store.directives(campaign_id)
            ):
                return 200, {"ok": True}  # already held
            sticky = kind == "pause" or (kind == "steer" and bool(body.get("sticky")))
            directive_id = self.store.add_directive(
                campaign_id, kind, payload, sticky=sticky
            )
            self.store.add_event(
                campaign_id,
                "directive_added",
                {"id": directive_id, "kind": kind, "payload": payload, "sticky": sticky},
            )
        return 200, {"ok": True, "id": directive_id}

    def cancel_directive(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        with self.lock:
            row = self.store._row(
                "SELECT * FROM directives WHERE id = ?", (body.get("id"),)
            )
            if row is None or row["status"] != "pending":
                return 404, {"error": "no such pending directive"}
            self.store.resolve_directive(row["id"], "cancelled")
            self.store.add_event(
                row["campaign_id"],
                "directive_cancelled",
                {"id": row["id"], "kind": row["kind"]},
            )
        return 200, {"ok": True}

    def retract_learning(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        with self.lock:
            row = self.store._row(
                "SELECT * FROM learnings WHERE id = ?", (body.get("id"),)
            )
            if row is None:
                return 404, {"error": "no such learning"}
            reason = str(body.get("reason") or "")
            self.store.retract_learning(row["id"], reason)
            self.store.add_event(
                row["campaign_id"],
                "learning_retracted",
                {"id": row["id"], "text": row["text"], "reason": reason},
            )
        return 200, {"ok": True}


def make_handler(watcher: Watcher):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # the page polls every second
            pass

        def _send(self, status: int, body: Any, kind: str = "application/json") -> None:
            data = body if isinstance(body, bytes) else json.dumps(
                body, default=str
            ).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            query = parse_qs(url.query)

            def number(name: str) -> Optional[int]:
                try:
                    return int(query[name][0])
                except (KeyError, ValueError):
                    return None

            if url.path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/info":
                self._send(200, watcher.info())
            elif url.path == "/api/campaigns":
                self._send(200, watcher.campaigns())
            elif url.path == "/api/state" and number("campaign") is not None:
                state = watcher.state(number("campaign"))
                self._send(200 if state else 404, state or {"error": "not found"})
            elif url.path == "/api/events" and number("campaign") is not None:
                self._send(200, watcher.events(number("campaign"), number("after")))
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            routes = {
                "/api/directive": watcher.add_directive,
                "/api/directive/cancel": watcher.cancel_directive,
                "/api/learning/retract": watcher.retract_learning,
                "/api/objective/compile": watcher.compile,
                "/api/campaigns": watcher.create_campaign,
                "/api/runs": watcher.start_run,
            }
            handler = routes.get(urlparse(self.path).path)
            if handler is None:
                return self._send(404, {"error": "not found"})
            # Direction changes what a run does, so only this machine's own
            # page may send it: other sites cannot post JSON here unasked.
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self._send(415, {"error": "send JSON"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("body must be an object")
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            self._send(*handler(body))

    return Handler


def make_server(
    db_path: str, port: int = 8765, config: Optional[ScientistConfig] = None
) -> ThreadingHTTPServer:
    """Server bound to localhost only; ``port=0`` picks a free port."""
    return ThreadingHTTPServer(
        ("127.0.0.1", port), make_handler(Watcher(Store(db_path), config))
    )


def serve(db_path: str, port: int = 8765, config: Optional[ScientistConfig] = None) -> None:
    server = make_server(db_path, port, config)
    logger.info(
        f"Watching {db_path} at http://127.0.0.1:{server.server_address[1]} "
        "(Ctrl-C to stop)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
