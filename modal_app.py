"""Modal app for running the AI Scientist in the cloud."""

import modal

# Define the image with all dependencies
# ggen requires: pyxtal, ase, orb-models, scipy
image = modal.Image.debian_slim(python_version="3.11").pip_install(
    # scientist dependencies
    "dspy-ai>=2.0.0",
    "numpy>=1.24",
    "openai>=1.0.0",
    "python-dotenv>=1.0.0",
    "ouro-py>=0.3.13",
    "pymatgen>=2024.12.1",
    "mlflow>=2.21.1",
    "pandas>=2.2.2",
    # ggen dependencies
    "pyxtal>=0.5.0",
    "ase>=3.22.0",
    "orb-models>=0.1.0",
    "scipy>=1.7.0",
    "requests>=2.25.0",
)

app = modal.App("scientist", image=image)

# Mount the local scientist and ggen packages
scientist_mount = modal.Mount.from_local_dir(
    "scientist",
    remote_path="/root/scientist",
)

ggen_mount = modal.Mount.from_local_dir(
    "../ggen/ggen",
    remote_path="/root/ggen",
)


@app.function(
    secrets=[modal.Secret.from_name("scientist-secrets")],
    mounts=[scientist_mount, ggen_mount],
    timeout=3600 * 6,  # 6 hour timeout for long-running discovery
    cpu=4,
    memory=8192,
)
def run_scientist():
    """Run the AI Scientist for material discovery."""
    import sys

    sys.path.insert(0, "/root")  # For both scientist and ggen packages

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
        logger.info(f"Space Group: {best['space_group_used']}")
        logger.info(f"Hypothesis: {best['hypothesis']}")
        logger.info(f"Score: {best['score']:.3f}")
        logger.info("Predicted Properties:")
        for prop, value in best["properties"].items():
            logger.info(
                f"  - {prop}: {value:.2f}"
                if isinstance(value, float)
                else f"  - {prop}: {value}"
            )

        # Log mutation effectiveness statistics
        logger.info("=" * 70)
        logger.info("MUTATION EFFECTIVENESS STATISTICS:")
        try:
            stats = scientist.tools.get_mutation_effectiveness_stats()
            logger.info("Mutation details:")
            if stats.get("num_operations"):
                avg_ops = sum(stats["num_operations"]) / len(stats["num_operations"])
                logger.info(f"  - Average operations per mutation set: {avg_ops:.1f}")
            else:
                logger.info("  - No successful mutations performed this run")
            if "operation_types" in stats and stats["operation_types"]:
                logger.info("  - Most common operation types:")
                sorted_ops = sorted(
                    stats["operation_types"].items(),
                    key=lambda x: x[1],
                    reverse=True,
                )
                for op_type, count in sorted_ops[:5]:
                    logger.info(f"    * {op_type}: {count} times")
        except Exception as e:
            logger.exception(f"Could not retrieve mutation statistics: {e}")

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
