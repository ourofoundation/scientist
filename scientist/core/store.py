"""Private SQLite memory: campaigns, the search tree, candidates, learnings.

A campaign is one research goal pursued across many runs. Each step of the
search adds a node (a move from a parent node, and what came of it), so the
path travelled survives between runs and informs the next step.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    goal TEXT NOT NULL,
    objective TEXT,
    current_node_id INTEGER,
    best_node_id INTEGER,
    best_score REAL NOT NULL DEFAULT 0,
    temperature REAL,
    steps_done INTEGER NOT NULL DEFAULT 0,
    steps_since_best INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    post_id TEXT,
    summary TEXT
);
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    parent_id INTEGER,
    step INTEGER NOT NULL,
    kind TEXT NOT NULL,
    system TEXT,
    move TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    score REAL NOT NULL DEFAULT 0,
    accepted INTEGER NOT NULL DEFAULT 0,
    temperature REAL,
    summary TEXT,
    assets TEXT,
    research TEXT,
    outcome TEXT,
    surprise REAL,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL,
    node_id INTEGER NOT NULL,
    formula TEXT NOT NULL,
    system TEXT,
    identity TEXT,
    origin TEXT,
    cif TEXT,
    file_id TEXT,
    properties TEXT NOT NULL DEFAULT '{}',
    errors TEXT NOT NULL DEFAULT '{}',
    artifacts TEXT NOT NULL DEFAULT '{}',
    score REAL NOT NULL DEFAULT 0,
    evaluated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    node_id INTEGER,
    purpose TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS route_calls (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    node_id INTEGER,
    route TEXT NOT NULL,
    action_id TEXT,
    status TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    cost_sats INTEGER NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learnings (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL,
    node_id INTEGER,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_JSON_COLUMNS = {
    "objective",
    "move",
    "summary",
    "assets",
    "properties",
    "errors",
    "artifacts",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        # Sub-agent tools read from worker threads (serialised by their own lock).
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(candidates)")}
        for column in ("identity", "origin"):
            if column not in columns:
                self.db.execute(f"ALTER TABLE candidates ADD COLUMN {column} TEXT")

    # -- plumbing ---------------------------------------------------------

    def _rows(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        return [self._decode(r) for r in self.db.execute(sql, params).fetchall()]

    def _row(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        rows = self._rows(sql, params)
        return rows[0] if rows else None

    @staticmethod
    def _decode(row: sqlite3.Row) -> Dict[str, Any]:
        out = dict(row)
        for key in _JSON_COLUMNS & out.keys():
            if out[key] is not None:
                out[key] = json.loads(out[key])
        return out

    def _insert(self, table: str, values: Dict[str, Any]) -> int:
        values = self._encode(values)
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        cur = self.db.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values())
        )
        self.db.commit()
        return int(cur.lastrowid)

    def _update(self, table: str, row_id: int, values: Dict[str, Any]) -> None:
        if not values:
            return
        values = self._encode(values)
        sets = ", ".join(f"{k} = ?" for k in values)
        self.db.execute(
            f"UPDATE {table} SET {sets} WHERE id = ?", (*values.values(), row_id)
        )
        self.db.commit()

    @staticmethod
    def _encode(values: Dict[str, Any]) -> Dict[str, Any]:
        return {
            k: json.dumps(v, default=str)
            if k in _JSON_COLUMNS and v is not None
            else v
            for k, v in values.items()
        }

    # -- campaigns and runs -----------------------------------------------

    def get_campaign(self, key: str) -> Optional[Dict[str, Any]]:
        return self._row("SELECT * FROM campaigns WHERE key = ?", (key,))

    def campaign(self, campaign_id: int) -> Dict[str, Any]:
        return self._row("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))

    def list_campaigns(self) -> List[Dict[str, Any]]:
        return self._rows("SELECT * FROM campaigns ORDER BY id")

    def create_campaign(self, key: str, goal: str) -> Dict[str, Any]:
        cid = self._insert(
            "campaigns", {"key": key, "goal": goal, "created_at": _now()}
        )
        return self.campaign(cid)

    def update_campaign(self, campaign_id: int, **values: Any) -> None:
        self._update("campaigns", campaign_id, values)

    def start_run(self, campaign_id: int) -> int:
        return self._insert(
            "runs", {"campaign_id": campaign_id, "started_at": _now()}
        )

    def finish_run(self, run_id: int, **values: Any) -> None:
        self._update("runs", run_id, {"finished_at": _now(), **values})

    def runs(self, campaign_id: int) -> List[Dict[str, Any]]:
        return self._rows(
            "SELECT * FROM runs WHERE campaign_id = ? ORDER BY id", (campaign_id,)
        )

    # -- search tree ------------------------------------------------------

    def add_node(self, **values: Any) -> int:
        return self._insert("nodes", {"created_at": _now(), **values})

    def update_node(self, node_id: int, **values: Any) -> None:
        self._update("nodes", node_id, values)

    def node(self, node_id: int) -> Optional[Dict[str, Any]]:
        return self._row("SELECT * FROM nodes WHERE id = ?", (node_id,))

    def nodes(
        self, campaign_id: int, run_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        if run_id is not None:
            return self._rows(
                "SELECT * FROM nodes WHERE campaign_id = ? AND run_id = ? ORDER BY id",
                (campaign_id, run_id),
            )
        return self._rows(
            "SELECT * FROM nodes WHERE campaign_id = ? ORDER BY id", (campaign_id,)
        )

    def path_to(self, node_id: Optional[int]) -> List[Dict[str, Any]]:
        """Nodes from the root down to ``node_id``."""
        path: List[Dict[str, Any]] = []
        seen = set()
        while node_id is not None and node_id not in seen:
            seen.add(node_id)
            node = self.node(node_id)
            if node is None:
                break
            path.append(node)
            node_id = node["parent_id"]
        return list(reversed(path))

    # -- candidates -------------------------------------------------------

    def add_candidate(self, **values: Any) -> int:
        return self._insert("candidates", {"created_at": _now(), **values})

    def update_candidate(self, candidate_id: int, **values: Any) -> None:
        self._update("candidates", candidate_id, values)

    def candidate(self, candidate_id: int) -> Optional[Dict[str, Any]]:
        return self._row("SELECT * FROM candidates WHERE id = ?", (candidate_id,))

    def candidates(
        self, campaign_id: int, node_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        if node_id is not None:
            return self._rows(
                "SELECT * FROM candidates WHERE campaign_id = ? AND node_id = ? "
                "ORDER BY score DESC, id",
                (campaign_id, node_id),
            )
        return self._rows(
            "SELECT * FROM candidates WHERE campaign_id = ? ORDER BY score DESC, id",
            (campaign_id,),
        )

    # -- learnings --------------------------------------------------------

    def add_learning(self, campaign_id: int, node_id: Optional[int], text: str) -> int:
        return self._insert(
            "learnings",
            {
                "campaign_id": campaign_id,
                "node_id": node_id,
                "text": text,
                "created_at": _now(),
            },
        )

    def learnings(self, campaign_id: int, limit: int = 40) -> List[Dict[str, Any]]:
        rows = self._rows(
            "SELECT * FROM learnings WHERE campaign_id = ? ORDER BY id DESC LIMIT ?",
            (campaign_id, limit),
        )
        return list(reversed(rows))

    def other_campaign_learnings(
        self, campaign_id: int, limit: int = 15
    ) -> List[Dict[str, Any]]:
        """Recent learnings from other goals — chemistry facts often transfer."""
        return self._rows(
            "SELECT l.text, c.goal FROM learnings l "
            "JOIN campaigns c ON c.id = l.campaign_id "
            "WHERE l.campaign_id != ? ORDER BY l.id DESC LIMIT ?",
            (campaign_id, limit),
        )

    # -- costs -----------------------------------------------------------

    def add_llm_calls(
        self,
        campaign_id: int,
        run_id: int,
        node_id: Optional[int],
        calls: List[Dict[str, Any]],
    ) -> None:
        for call in calls:
            self._insert(
                "llm_calls",
                {
                    "campaign_id": campaign_id,
                    "run_id": run_id,
                    "node_id": node_id,
                    "created_at": _now(),
                    **call,
                },
            )

    def add_route_calls(
        self,
        campaign_id: int,
        run_id: int,
        node_id: Optional[int],
        calls: List[Dict[str, Any]],
    ) -> None:
        for call in calls:
            self._insert(
                "route_calls",
                {
                    "campaign_id": campaign_id,
                    "run_id": run_id,
                    "node_id": node_id,
                    "created_at": _now(),
                    **call,
                },
            )

    def usage(self, run_id: int) -> Dict[str, Any]:
        """What a run has cost so far: LLM by purpose, Ouro compute by route."""
        llm = self._row(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens), 0) AS input_tokens, "
            "COALESCE(SUM(cached_tokens), 0) AS cached_tokens, "
            "COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens, "
            "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
            "COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens, "
            "COALESCE(SUM(cost_usd), 0) AS cost_usd FROM llm_calls WHERE run_id = ?",
            (run_id,),
        )
        llm["by_purpose"] = self._rows(
            "SELECT purpose, COUNT(*) AS calls, SUM(cost_usd) AS cost_usd "
            "FROM llm_calls WHERE run_id = ? GROUP BY purpose ORDER BY cost_usd DESC",
            (run_id,),
        )
        routes = self._row(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(cost_usd), 0) AS cost_usd, "
            "COALESCE(SUM(cost_sats), 0) AS cost_sats, "
            "COALESCE(SUM(seconds), 0) AS seconds FROM route_calls WHERE run_id = ?",
            (run_id,),
        )
        routes["by_route"] = self._rows(
            "SELECT route, COUNT(*) AS calls, SUM(cost_usd) AS cost_usd, "
            "SUM(cost_sats) AS cost_sats, SUM(seconds) AS seconds "
            "FROM route_calls WHERE run_id = ? GROUP BY route ORDER BY seconds DESC",
            (run_id,),
        )
        return {"llm": llm, "routes": routes}

    def close(self) -> None:
        self.db.close()
