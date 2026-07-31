"""Modal app for running the AI Scientist in the cloud."""

import modal

# GGen runs remotely via Ouro-hosted routes — no local GPU/MLIP stack needed.
image = modal.Image.debian_slim(python_version="3.11").pip_install(
    "dspy-ai>=2.0.0",
    "numpy>=1.24",
    "openai>=1.0.0",
    "python-dotenv>=1.0.0",
    "ouro-py>=0.3.13",
    "pymatgen>=2024.12.1",
    "mlflow>=2.21.1",
    "pandas>=2.2.2",
    "requests>=2.25.0",
)

app = modal.App("scientist", image=image)

scientist_mount = modal.Mount.from_local_dir(
    "scientist",
    remote_path="/root/scientist",
)


@app.function(
    secrets=[modal.Secret.from_name("scientist-secrets")],
    mounts=[scientist_mount],
    timeout=3600 * 12,  # hosted GGen explorations are long-running
    cpu=2,
    memory=4096,
)
def run_scientist():
    """Run the AI Scientist for material discovery."""
    import sys

    sys.path.insert(0, "/root")

    import dspy
    import mlflow

    from scientist.core.config import ScientistConfig
    from scientist.core.scientist import MaterialDiscoveryScientist
    from scientist.utils.publisher import Publisher
    from scientist.utils.logging import setup_logging, get_logger

    # Initialize logging
    setup_logging()
    logger = get_logger("main")

    # Load configuration
    config = ScientistConfig.from_env()

    # Configure MLflow - use a remote tracking server or disable for Modal
    # mlflow.set_tracking_uri(config.mlflow_tracking_uri)
    # mlflow.set_experiment(config.mlflow_experiment)

    # Configure DSPy with LLM
    lm = dspy.LM(
        config.llm_model,
        api_key=config.llm_api_key,
        max_tokens=config.llm_max_tokens,
        cache=config.llm_cache,
        temperature=config.llm_temperature,
    )
    dspy.settings.configure(lm=lm)

    # Create an initial Ouro post to parent downstream assets
    publisher = Publisher(config)
    initial_post = publisher.create_initial_post(targets=config.default_targets)

    initial_post_id = (
        initial_post.get("id")
        if isinstance(initial_post, dict)
        else getattr(initial_post, "id", None)
    )

    # Initialize AI Scientist with post id for asset parenting
    scientist = MaterialDiscoveryScientist(config, post_id=initial_post_id)

    logger.info("Starting AI Scientist for Rare-Earth-Free Permanent Magnet Discovery")
    logger.info("=" * 70)

    # Run discovery
    discovery = scientist(target_properties=config.default_targets)

    # Log results
    if discovery["best_material"]:
        best = discovery["best_material"]
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

    # Publish run summary to Ouro
    try:
        publisher.publish_run_summary(
            discovery=discovery,
            targets=config.default_targets,
            post_id=initial_post_id,
        )
    except Exception as e:
        logger.exception(f"Publishing to Ouro failed: {e}")

    return discovery


@app.local_entrypoint()
def main():
    """Local entrypoint to trigger the scientist run."""
    print("Starting AI Scientist on Modal...")
    result = run_scientist.remote()
    print("Discovery complete!")
    if result and result.get("best_material"):
        best = result["best_material"]
        print(f"\nBest material found: {best['composition']}")
        print(f"Score: {best['score']:.3f}")
    return result
