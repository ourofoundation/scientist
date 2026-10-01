"""Read-only tools a sub-agent can be given."""

from __future__ import annotations

import html
import re
import sqlite3
import threading
from typing import Any, Optional

import requests
from pydantic import BaseModel, Field

from .agent import Tool

USER_AGENT = "ouro-scientist/0.3 (research agent; +https://ouro.foundation)"


class SearchArgs(BaseModel):
    query: str = Field(description="what to look for, as a specific question")


class ReadArgs(BaseModel):
    url: str = Field(description="http(s) URL of a page to read")


class SqlArgs(BaseModel):
    sql: str = Field(description="a single SQLite SELECT statement")


def web_search_tool(llm, model: Optional[str] = None) -> Tool:
    """Web search answered by a search-grounded model call, with sources."""

    def run(args: SearchArgs) -> str:
        return llm.text(
            "Search the web and report what you find for the query. Give "
            "specific facts and numbers, each with the URL it came from. Say "
            "so plainly when the search turns up nothing reliable.",
            args.query,
            purpose="web_search",
            web=True,
            model=model,
        )

    return Tool(
        name="web_search",
        description="Search the web (papers, databases, reviews). Returns a "
        "sourced summary for one specific question. Ask separate questions "
        "in separate calls.",
        parameters=SearchArgs,
        run=run,
    )


def read_url_tool(max_chars: int = 20_000) -> Tool:
    """Fetch a page and return its text."""

    def run(args: ReadArgs) -> str:
        if not re.match(r"^https?://", args.url):
            return "Only http(s) URLs can be read."
        response = requests.get(
            args.url, timeout=30, headers={"User-Agent": USER_AGENT}
        )
        response.raise_for_status()
        kind = response.headers.get("content-type", "")
        if "pdf" in kind:
            return (
                "This is a PDF, which cannot be read here. Try the abstract "
                "or HTML version of the page instead."
            )
        text = re.sub(r"(?is)<(script|style|nav|footer|head)\b.*?</\1>", " ", response.text)
        text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", text))
        return re.sub(r"\s+", " ", text).strip()[:max_chars]

    return Tool(
        name="read_url",
        description="Read the text of a web page, such as a paper abstract "
        "page found by web_search. PDFs are not supported.",
        parameters=ReadArgs,
        run=run,
    )


def query_memory_tool(store, campaign_id: int, max_rows: int = 200) -> Tool:
    """Read-only SQL over the search memory."""

    lock = threading.Lock()  # tool calls in one turn run on worker threads

    def run(args: SqlArgs) -> str:
        sql = args.sql.strip().rstrip(";")
        if not re.match(r"(?is)^\s*(select|with)\b", sql) or ";" in sql:
            return "Only a single SELECT statement is allowed."
        with lock:
            store.db.execute("PRAGMA query_only = ON")
            try:
                cursor = store.db.execute(sql)
                rows = cursor.fetchmany(max_rows + 1)
                columns = [c[0] for c in cursor.description]
            except sqlite3.Error as e:
                return f"SQL error: {e}"
            finally:
                store.db.execute("PRAGMA query_only = OFF")

        lines = [" | ".join(columns)]
        lines += [" | ".join(_cell(v) for v in row) for row in rows[:max_rows]]
        if len(rows) > max_rows:
            lines.append(f"… more than {max_rows} rows; narrow the query")
        return "\n".join(lines)

    return Tool(
        name="query_memory",
        description=(
            "Run a read-only SQLite SELECT over everything this scientist has "
            f"recorded. This campaign has campaign_id = {campaign_id}; other "
            "campaigns pursued other goals but their measurements are just as "
            "real. Tables:\n"
            "- campaigns(id, goal, best_score, steps_done)\n"
            "- nodes(id, campaign_id, run_id, parent_id, kind, system, status, "
            "score, accepted (1 = set a new best), outcome, surprise, error, move, research)\n"
            "- candidates(id, campaign_id, node_id, formula, system, score, "
            "evaluated, properties, errors) — properties is JSON, e.g. "
            "json_extract(properties, '$.curie_temperature'). Do not select "
            "the cif column.\n"
            "- learnings(id, campaign_id, node_id, text)"
        ),
        parameters=SqlArgs,
        run=run,
    )


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= 400 else text[:400] + "…"
