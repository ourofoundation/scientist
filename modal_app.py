"""Modal app for running the AI Scientist on a schedule.

Deploy (schedules only fire after deploy, not serve):

    MODAL_PROFILE=ouro-users modal deploy modal_app.py

Manual run, optionally on a different goal:

    MODAL_PROFILE=ouro-users modal run modal_app.py
    MODAL_PROFILE=ouro-users modal run modal_app.py --goal "Find a Gd-based ..."

The `scientist-secrets` secret needs OPENROUTER_API_KEY, OURO_API_KEY and
OURO_TEAM_ID. Set SCIENTIST_GOAL there to change what the daily run pursues.
"""

import modal

# GGen runs remotely via Ouro-hosted routes — no local GPU/MLIP stack needed.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "openai>=1.0.0",
        "pydantic>=2.0",
        "python-dotenv>=1.0.0",
        "ouro-py>=2.0.0",
        "websocket-client>=1.6.0",
        "pandas>=2.2.2",
        "requests>=2.25.0",
    )
    .add_local_dir("scientist", remote_path="/root/scientist")
)

app = modal.App("scientist", image=image)

# The search memory (SQLite) persists here between runs.
memory = modal.Volume.from_name("scientist-memory", create_if_missing=True)
MEMORY_DIR = "/memory"

# Daily at 06:00 UTC. max_containers=1 so a long GGen run cannot overlap the
# next, and so only one writer ever touches the memory database.
SCHEDULE = modal.Cron("0 6 * * *")


@app.function(
    secrets=[modal.Secret.from_name("scientist-secrets")],
    volumes={MEMORY_DIR: memory},
    timeout=60 * 60 * 24,
    cpu=2,
    memory=4096,
    max_containers=1,
    schedule=SCHEDULE,
)
def run_scientist(goal: str = "", campaign: str = ""):
    """Run one budgeted search and publish the report to Ouro."""
    import os
    import sys

    sys.path.insert(0, "/root")
    os.environ["SCIENTIST_DB"] = f"{MEMORY_DIR}/scientist.db"

    from scientist.core.config import DEFAULT_GOAL
    from scientist.main import run_campaign
    from scientist.utils.logging import setup_logging

    setup_logging()
    goal = goal or os.getenv("SCIENTIST_GOAL") or DEFAULT_GOAL
    try:
        result = run_campaign(goal, campaign or None)
    finally:
        memory.commit()

    best = result["best_candidate"]
    return {
        "post_id": result.get("post_id"),
        "steps": len(result["nodes"]),
        "campaign_steps": result["campaign"]["steps_done"],
        "best": best
        and {
            "formula": best["formula"],
            "chemical_system": best["system"],
            "score": best["score"],
        },
    }


@app.local_entrypoint()
def main(goal: str = "", campaign: str = ""):
    """Trigger one scientist run without waiting for the cron."""
    print("Starting AI Scientist on Modal...")
    result = run_scientist.remote(goal, campaign)
    print(f"Run complete: {result}")
    return result
