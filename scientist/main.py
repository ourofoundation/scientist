"""Main entry point for the AI Scientist."""

import dspy
import mlflow
from .core.config import ScientistConfig
from .core.scientist import MaterialDiscoveryScientist
from .utils.publisher import Publisher


def main():
    """Main execution function."""
    # Load configuration
    config = ScientistConfig.from_env()

    # Configure MLflow
    mlflow.dspy.autolog(
        log_compiles=True,
        log_evals=True,
        log_traces_from_compile=True,
        log_traces=True,
    )
    mlflow.set_tracking_uri(config.mlflow_tracking_uri)
    mlflow.set_experiment(config.mlflow_experiment)

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

    print("Starting AI Scientist for Rare-Earth-Free Permanent Magnet Discovery")
    print("=" * 70)

    # Run discovery
    discovery = scientist(target_properties=config.default_targets)

    # Print results
    if discovery["best_material"]:
        best = discovery["best_material"]
        print("\n" + "=" * 70)
        print("BEST DISCOVERY:")
        print(f"Composition: {best['composition']}")
        print(f"Space Group: {best['space_group_used']}")
        print(f"Hypothesis: {best['hypothesis']}")
        print(f"Score: {best['score']:.3f}")
        print("Predicted Properties:")
        for prop, value in best["properties"].items():
            print(f"  - {prop}: {value:.2f}")

        # Print mutation effectiveness statistics
        print("\n" + "=" * 70)
        print("MUTATION EFFECTIVENESS STATISTICS:")
        try:
            stats = scientist.tools.get_mutation_effectiveness_stats()
            print(f"\Mutation details:")
            if stats.get("num_operations"):
                avg_ops = sum(stats["num_operations"]) / len(stats["num_operations"])
                print(f"  - Average operations per mutation set: {avg_ops:.1f}")
            else:
                print(f"  - No successful mutations performed this run")
            if "operation_types" in stats and stats["operation_types"]:
                print(f"  - Most common operation types:")
                sorted_ops = sorted(
                    stats["operation_types"].items(),
                    key=lambda x: x[1],
                    reverse=True,
                )
                for op_type, count in sorted_ops[:5]:  # Show top 5
                    print(f"    * {op_type}: {count} times")
        except Exception as e:
            import traceback

            traceback.print_exc()
            print(f"Could not retrieve mutation statistics: {e}")

    # Publish run summary to Ouro (update initial post if available)
    try:
        publisher.publish_run_summary(
            discovery=discovery,
            targets=config.default_targets,
            post_id=initial_post_id,
        )
    except Exception as _publish_exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"Publishing to Ouro failed: {_publish_exc}")


if __name__ == "__main__":
    main()
