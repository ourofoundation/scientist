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
    if config.sim:
        return _run_simulated(goal, campaign, config)
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


def _run_simulated(
    goal: str, campaign: Optional[str], config: ScientistConfig, oracle: bool = False
) -> Dict[str, Any]:
    """The same search against the simulated world; nothing is sent to Ouro."""
    from .computational.sim import SimEvaluator, SimExplorer, SimWorld
    from .core.search import campaign_key

    world = SimWorld(config.sim_seed, failure_rate=config.sim_failure_rate)
    explorer = SimExplorer(
        world,
        max_atoms=config.ggen_max_atoms,
        num_trials=config.ggen_num_trials,
        e_hull_cutoff=config.ggen_e_hull_cutoff,
        max_candidates=config.max_candidates_stored,
    )
    llm = LLM(config)
    store = Store(config.db_path)
    search = Search(config, llm, store, explorer, SimEvaluator(world), meter=world)
    logger.info(
        f"Simulated world (seed {config.sim_seed}), model {config.llm_model}, "
        f"memory {config.db_path}"
    )

    record = search.open_campaign(goal, campaign or f"sim-{campaign_key(goal)}")
    run_id = store.start_run(record["id"])
    result = search.run(record["id"], run_id)
    _log_result(result)

    if oracle:
        objective = ObjectiveSpec.model_validate(result["campaign"]["objective"])
        found = result["best_candidate"]["score"] if result["best_candidate"] else 0.0
        logger.info("Best possible in this world (up to 3 elements):")
        for entry in world.oracle(objective):
            logger.info(
                f"  {entry['formula']} [{entry['system']}] score "
                f"{entry['score']:.3f} {entry['properties']}"
            )
            result.setdefault("oracle", []).append(entry)
        logger.info(f"Found {found:.3f} of {result['oracle'][0]['score']:.3f}")
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
    verdict = result.get("verdict") or {}
    logger.info(f"Goal: {verdict.get('status', 'unknown').replace('_', ' ')}")
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
        "--expensive",
        type=int,
        help="expensive (multi-hour) calculations allowed this run",
    )
    parser.add_argument(
        "--list", action="store_true", help="list campaigns in memory and exit"
    )
    parser.add_argument("--model", help="OpenRouter model id for this run")
    parser.add_argument(
        "--sim",
        action="store_true",
        help="run against the simulated world: instant, free calculations, a "
        "cheap model, separate memory, nothing sent to Ouro",
    )
    parser.add_argument(
        "--seed", type=int, default=0, help="which simulated world (with --sim)"
    )
    parser.add_argument(
        "--oracle",
        action="store_true",
        help="with --sim, also report the best result the world contains",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="serve the live view of this memory (add --sim for the simulated "
        "one) instead of running; start runs from another terminal",
    )
    parser.add_argument("--port", type=int, default=8765, help="port for --watch")
    args = parser.parse_args()

    setup_logging()
    if args.watch:
        from .watch import serve

        config = ScientistConfig()
        if args.sim:
            config.use_sim(args.seed)
        if args.model:
            config.llm_model = args.model
        serve(config.db_path, args.port, config)
        return
    if args.list:
        config = ScientistConfig()
        if args.sim:
            config.use_sim(args.seed)
        _list_campaigns(config)
        return

    config = ScientistConfig.from_env(sim=args.sim, sim_seed=args.seed)
    if args.steps:
        config.max_steps = args.steps
    if args.expensive is not None:
        config.max_expensive_evals = args.expensive
    if args.model:
        config.llm_model = args.model
    if args.sim:
        _run_simulated(args.goal, args.campaign, config, oracle=args.oracle)
    else:
        run_campaign(args.goal, args.campaign, config)


if __name__ == "__main__":
    main()
