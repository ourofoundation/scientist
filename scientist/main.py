"""Main entry point for the AI Scientist."""

import argparse
from typing import Any, Dict, Optional

from .computational.catalog import CATALOG
from .core.config import DEFAULT_GOAL, ScientistConfig
from .core.llm import LLM
from .core.objective import ObjectiveSpec
from .core.search import Search
from .core.store import Store
from .utils.logging import get_logger, setup_logging

logger = get_logger("main")


def run_campaign(
    goal: str,
    campaign: Optional[str] = None,
    config: Optional[ScientistConfig] = None,
) -> Dict[str, Any]:
    """Run one budgeted search on a goal, resuming its campaign if one exists."""
    from .computational.evaluator import Evaluator
    from .computational.explorer import SystemExplorer
    from .computational.ouro_client import OuroClient
    from .utils.report import Reporter

    config = config or ScientistConfig.from_env()
    llm = LLM(config)
    store = Store(config.db_path)
    ouro = OuroClient(
        team_id=config.ouro_team_id, visibility=config.ouro_asset_visibility
    )
    explorer = SystemExplorer(
        ouro,
        max_atoms=config.ggen_max_atoms,
        min_atoms=config.ggen_min_atoms,
        num_trials=config.ggen_num_trials,
        e_hull_cutoff=config.ggen_e_hull_cutoff,
        max_stoichiometries=config.ggen_max_stoichiometries,
        max_candidates=config.max_candidates_stored,
        poll_timeout=config.ggen_poll_timeout,
    )
    # A renamed route would otherwise surface as every measurement failing.
    missing = ouro.missing_routes(
        sorted({p.route for p in CATALOG.values() if p.route})
    )
    if missing:
        raise RuntimeError(f"Catalog routes not available on Ouro: {missing}")

    search = Search(config, llm, store, explorer, Evaluator(ouro), meter=ouro)
    reporter = Reporter(config, llm, store)

    record = search.open_campaign(goal, campaign)
    run_id = store.start_run(record["id"])

    # Assets created during the run are parented to the run's post.
    post_id = reporter.start(record)
    ouro.post_id = post_id
    store.finish_run(run_id, post_id=post_id)

    result = search.run(record["id"], run_id)
    result["post_id"] = post_id
    try:
        reporter.publish(result, post_id)
    except Exception as e:
        logger.exception(f"Publishing to Ouro failed: {e}")
    search._save_costs(record["id"], run_id)  # the report's own calls
    result["usage"] = store.usage(run_id)

    best = result["best_candidate"]
    store.finish_run(
        run_id,
        summary={
            "steps": len(result["nodes"]),
            "best": best and {"formula": best["formula"], "score": best["score"]},
            "usage": result["usage"],
        },
    )
    _log_result(result)
    store.close()
    return result


def _log_result(result: Dict[str, Any]) -> None:
    campaign, best, usage = (
        result["campaign"],
        result["best_candidate"],
        result["usage"],
    )
    logger.info("=" * 70)
    logger.info(
        f"Run complete: {len(result['nodes'])} steps "
        f"({campaign['steps_done']} in campaign), post {result.get('post_id')}"
    )
    if best:
        logger.info(
            f"Best candidate: {best['formula']} [{best['system']}] "
            f"score {best['score']:.3f}"
        )
        for name, value in best["properties"].items():
            logger.info(f"  - {name}: {value}")
    llm, routes = usage["llm"], usage["routes"]
    logger.info(
        f"LLM: {llm['calls']} calls, ${llm['cost_usd']:.2f} — "
        f"{llm['input_tokens']:,} in ({llm['cached_tokens']:,} cached), "
        f"{llm['output_tokens']:,} out"
    )
    for row in llm["by_purpose"]:
        logger.info(f"  - {row['purpose']}: {row['calls']} calls, ${row['cost_usd']:.2f}")
    logger.info(
        f"Ouro routes: {routes['calls']} runs, ${routes['cost_usd']:.2f} + "
        f"{routes['cost_sats']:,} sats, {routes['seconds'] / 60:.0f} min of compute"
    )


def _list_campaigns(config: ScientistConfig) -> None:
    store = Store(config.db_path)
    for c in store.list_campaigns():
        title = (
            ObjectiveSpec.model_validate(c["objective"]).title
            if c["objective"]
            else "(objective not compiled)"
        )
        print(
            f"{c['key']}  {c['steps_done']:>3} steps  best {c['best_score']:.3f}  "
            f"{title}\n    {c['goal']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the AI Scientist on a materials research goal."
    )
    parser.add_argument("goal", nargs="?", default=DEFAULT_GOAL)
    parser.add_argument(
        "--campaign",
        help="campaign key to resume or create (default: derived from the goal)",
    )
    parser.add_argument("--steps", type=int, help="search steps this run")
    parser.add_argument(
        "--list", action="store_true", help="list campaigns in memory and exit"
    )
    args = parser.parse_args()

    setup_logging()
    if args.list:
        _list_campaigns(ScientistConfig())
        return

    config = ScientistConfig.from_env()
    if args.steps:
        config.max_steps = args.steps
    run_campaign(args.goal, args.campaign, config)


if __name__ == "__main__":
    main()
