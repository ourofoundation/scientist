"""Shared pieces of the lab scripts: the Ouro client, where a campaign's runs
go, and waiting on a run.

These scripts need ouro-py, pymatgen and crystaldb, so run them with the
materials pyenv (`~/.pyenv/versions/materials/bin/python`), not the system
Python that notebook.py is happy with.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
NOTEBOOK = Path(os.environ.get("SCIENTIST_NOTEBOOK") or REPO / "lab" / "notebook")
TERMINAL = ("success", "error", "timed-out", "cancelled")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def client():
    """An ouro-py client. OURO_API_KEY comes from the shell or the repo's .env."""
    env = REPO / ".env"
    if "OURO_API_KEY" not in os.environ and env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.strip().split("=", 1)
                os.environ.setdefault(key, value.strip("\"'"))
    if "OURO_API_KEY" not in os.environ:
        sys.exit("OURO_API_KEY is not set (shell or the repo's .env).")
    logging.getLogger("ouro").setLevel(logging.WARNING)
    from ouro import Ouro

    return Ouro(api_key=os.environ["OURO_API_KEY"])


def where(campaign: str) -> tuple[str, str]:
    """(org id, team id) from the 'Where runs go' line of a campaign.md."""
    path = NOTEBOOK / "campaigns" / campaign / "campaign.md"
    if not path.exists():
        sys.exit(f"No campaign at {path}")
    text = path.read_text()
    start = text.find("Where runs go")
    ids = UUID.findall(text[start : start + 600]) if start >= 0 else []
    if len(ids) < 2:
        sys.exit(f"{path}: 'Where runs go' needs an org id and a team id.")
    return ids[0], ids[1]


def wait(ouro, action_id: str, timeout: float = 1800, poll: float = 3):
    """Poll one run until it ends. Returns the action, or None on timeout:
    a timeout here says nothing about the run, so look it up again later."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll)
        try:
            action = ouro.routes.retrieve_action(action_id)
        except Exception:
            continue  # a dropped request is not a failed run
        if action.status in TERMINAL:
            return action
    return None


def payload(action) -> dict:
    """A finished run's response as a dict (its error body when it failed)."""
    data = action.final_data if action.status == "success" else action.response
    return data if isinstance(data, dict) else {"raw": str(data)[:500]}
