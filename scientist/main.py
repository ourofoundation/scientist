"""Main entry point for the AI Scientist."""

from typing import Dict, Any

import dspy
import mlflow

from .core.config import ScientistConfig
from .core.scientist import MaterialDiscoveryScientist
from .utils.publisher import Publisher
from .utils.logging import setup_logging, get_logger

setup_logging()
logger = get_logger("main")


def get_token_usage(lm: dspy.LM) -> Dict[str, Any]:
    """Extract token usage statistics from DSPy LM history."""
    total_input_tokens = 0
    total_output_tokens = 0
    total_calls = 0

    for entry in lm.history:
        usage = entry.get("usage", {})
        total_input_tokens += usage.get("prompt_tokens", 0) or usage.get(
            "input_tokens", 0
        )
        total_output_tokens += usage.get("completion_tokens", 0) or usage.get(
            "output_tokens", 0
        )
        total_calls += 1

    total_tokens = total_input_tokens + total_output_tokens
    # Rough GPT-4-class estimate; actual cost depends on model
    estimated_cost = (total_input_tokens / 1000 * 0.01) + (
        total_output_tokens / 1000 * 0.03
    )

    return {
        "total_calls": total_calls,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_usd": round(estimated_cost, 4),
    }


def main():
    """Main execution function."""
    config = ScientistConfig.from_env()

    mlflow.dspy.autolog(
        log_compiles=True,
        log_evals=True,
        log_traces_from_compile=True,
        log_traces=True,
    )
    mlflow.set_tracking_uri(config.mlflow_tracking_uri)
    mlflow.set_experiment(config.mlflow_experiment)

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

    token_usage = get_token_usage(lm)
    logger.info("=" * 70)
    logger.info("TOKEN USAGE:")
    logger.info(f"  Total LLM calls: {token_usage['total_calls']}")
    logger.info(f"  Input tokens: {token_usage['input_tokens']:,}")
    logger.info(f"  Output tokens: {token_usage['output_tokens']:,}")
    logger.info(f"  Total tokens: {token_usage['total_tokens']:,}")
    logger.info(f"  Estimated cost: ${token_usage['estimated_cost_usd']:.4f}")

    if discovery["best_material"]:
        best = discovery["best_material"]
        logger.info("=" * 70)
        logger.info("BEST DISCOVERY:")
        logger.info(f"Composition: {best['composition']}")
        logger.info(f"Chemical system: {best.get('chemical_system')}")
        logger.info(f"Space Group: {best.get('space_group_resolved') or best.get('space_group_used')}")
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
        for system in discovery.get("explored_systems", []):
            logger.info(f"  - {system}")
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
            token_usage=token_usage,
        )
    except Exception as e:
        logger.exception(f"Publishing to Ouro failed: {e}")


if __name__ == "__main__":
    main()
