"""Modal app for running the AI Scientist on a schedule.

Deploy (schedules only fire after deploy, not serve):

    MODAL_PROFILE=ouro-users modal deploy modal_app.py

Manual run:

    MODAL_PROFILE=ouro-users modal run modal_app.py
"""

import modal

# GGen runs remotely via Ouro-hosted routes — no local GPU/MLIP stack needed.
# ouro-py>=0.11 is required for wait=False / poll_action on long explore jobs.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "dspy-ai>=2.0.0",
        "numpy>=1.24",
        "openai>=1.0.0",
        "python-dotenv>=1.0.0",
        "ouro-py>=0.11.0",
        "pymatgen>=2024.12.1",
        "mlflow>=2.21.1",
        "pandas>=2.2.2",
        "requests>=2.25.0",
    )
    .add_local_dir("scientist", remote_path="/root/scientist")
)

app = modal.App("scientist", image=image)

# Daily at 06:00 UTC. max_containers=1 so a long GGen run cannot overlap the next.
SCHEDULE = modal.Cron("0 6 * * *")


@app.function(
    secrets=[modal.Secret.from_name("scientist-secrets")],
    timeout=60 * 60 * 24,
    cpu=2,
    memory=4096,
    max_containers=1,
    schedule=SCHEDULE,
)
def run_scientist():
    """Run one discovery loop and publish the summary to Ouro."""
    import sys

    sys.path.insert(0, "/root")

    import dspy

    from scientist.core.config import ScientistConfig
    from scientist.core.scientist import MaterialDiscoveryScientist
    from scientist.utils.publisher import Publisher
    from scientist.utils.logging import setup_logging, get_logger

    setup_logging()
    logger = get_logger("main")

    config = ScientistConfig.from_env()

    lm = dspy.LM(
        config.llm_model,
        api_key=config.llm_api_key,
        max_tokens=config.llm_max_tokens,
        cache=config.llm_cache,
        temperature=config.llm_temperature,
    )
    dspy.settings.configure(lm=lm)

    publisher = Publisher(config)
    initial_post = publisher.create_initial_post(targets=config.default_targets)
    initial_post_id = (
        initial_post.get("id")
        if isinstance(initial_post, dict)
        else getattr(initial_post, "id", None)
    )

    scientist = MaterialDiscoveryScientist(config, post_id=initial_post_id)

    logger.info("Starting AI Scientist for Rare-Earth-Free Permanent Magnet Discovery")
    logger.info("=" * 70)

    discovery = scientist(target_properties=config.default_targets)

    best = discovery.get("best_material")
    if best:
        logger.info("=" * 70)
        logger.info("BEST DISCOVERY:")
        logger.info(f"Composition: {best['composition']}")
        logger.info(f"Chemical system: {best.get('chemical_system')}")
        logger.info(
            f"Space Group: {best.get('space_group_resolved') or best.get('space_group_used')}"
        )
        logger.info(f"Hypothesis: {best['hypothesis']}")
        logger.info(f"Score: {best['score']:.3f}")
        logger.info("Predicted Properties:")
        for prop, value in best["properties"].items():
            logger.info(
                f"  - {prop}: {value:.2f}"
                if isinstance(value, float)
                else f"  - {prop}: {value}"
            )

        logger.info("=" * 70)
        logger.info("EXPLORATION SUMMARY:")
        for exp in discovery.get("exploration_history", []):
            logger.info(
                f"  {exp['chemical_system']}: "
                f"{exp['num_near_hull']} near-hull, "
                f"{exp['num_evaluated']} evaluated"
            )

    try:
        publisher.publish_run_summary(
            discovery=discovery,
            targets=config.default_targets,
            post_id=initial_post_id,
        )
    except Exception as e:
        logger.exception(f"Publishing to Ouro failed: {e}")

    return {
        "post_id": initial_post_id,
        "iterations_run": discovery.get("iterations_run"),
        "explored_systems": discovery.get("explored_systems"),
        "n_candidates": len(discovery.get("all_results") or []),
        "best": (
            {
                "composition": best["composition"],
                "chemical_system": best.get("chemical_system"),
                "score": best["score"],
            }
            if best
            else None
        ),
    }


@app.local_entrypoint()
def main():
    """Trigger one scientist run without waiting for the cron."""
    print("Starting AI Scientist on Modal...")
    result = run_scientist.remote()
    print("Discovery complete!")
    if result and result.get("best"):
        best = result["best"]
        print(f"\nBest material found: {best['composition']}")
        print(f"Score: {best['score']:.3f}")
    elif result:
        print("No best material this run.")
        print(f"Systems explored: {result.get('explored_systems')}")
    return result
