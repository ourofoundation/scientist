"""Tiny end-to-end smoke test of hosted-GGen scientist loop.

Resumes the Al-Fe explore action. If the action errored on asset materialization
but the webhook still carried a summary, recover from that and export CIFs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import dotenv

_env = Path(__file__).resolve().parent / ".env"
dotenv.load_dotenv(_env, override=True)
os.environ["OURO_BACKEND_URL"] = "https://api.ouro.foundation"
os.environ["OURO_BASE_URL"] = "https://api.ouro.foundation"

from scientist.core.config import ScientistConfig
from scientist.utils.logging import setup_logging, get_logger
from scientist.utils.publisher import Publisher
from scientist.computational.ouro_client import OuroClient
from scientist.computational.explorer import SystemExplorer
from scientist.computational.evaluator import MaterialEvaluator, Tier2Thresholds
from scientist.computational.registry import MaterialRegistry
from scientist.computational.scorer import MaterialScorer
from scientist.data.models import MaterialProperties

setup_logging()
logger = get_logger("smoke")

DEFAULT_RESUME_ACTION = "019fb39b-ef33-7718-a273-1965fcbf57f1"


def main() -> None:
    config = ScientistConfig.from_env()
    config.max_candidates_to_evaluate = 2
    config.ggen_e_hull_cutoff = 0.15
    config.ggen_poll_timeout = 60 * 45

    publisher = Publisher(config)
    initial_post = publisher.create_initial_post(
        run_title="Scientist smoke test (hosted GGen)",
        targets=config.default_targets,
        description="Tiny Al-Fe smoke run against Ouro-hosted GGen.",
    )
    post_id = (
        initial_post.get("id")
        if isinstance(initial_post, dict)
        else getattr(initial_post, "id", None)
    )
    logger.info(f"Initial post: {post_id}")

    ouro = OuroClient(
        team_id=config.ouro_team_id,
        visibility=config.ouro_asset_visibility,
        post_id=post_id,
    )
    registry = MaterialRegistry()
    explorer = SystemExplorer(
        ouro_client=ouro,
        registry=registry,
        e_hull_cutoff=config.ggen_e_hull_cutoff,
        max_candidates=config.max_candidates_to_evaluate,
        poll_timeout=config.ggen_poll_timeout,
    )

    action_id = os.getenv("SMOKE_EXPLORE_ACTION", DEFAULT_RESUME_ACTION)
    logger.info(f"Retrieving explore action {action_id}")
    action = ouro.ouro.routes.retrieve_action(action_id)

    summary = ouro.extract_explore_summary(action)
    if not summary.get("stable_phases"):
        # Still running — poll without raising so we can recover webhook payloads
        try:
            action = ouro.ouro.routes.poll_action(
                action_id,
                timeout=config.ggen_poll_timeout,
                raise_on_error=False,
            )
        except Exception as e:
            logger.warning(f"Poll raised: {e}")
            action = ouro.ouro.routes.retrieve_action(action_id)
        summary = ouro.extract_explore_summary(action)

    if not summary.get("stable_phases"):
        raise RuntimeError(
            f"No exploration summary on action {action_id} "
            f"(status={action.status})"
        )

    chemical_system = summary.get("chemical_system") or "Al-Fe"
    stable_phases = list(summary.get("stable_phases") or [])
    logger.info(
        f"Recovered summary: {chemical_system}, "
        f"{len(stable_phases)} near-hull, "
        f"{summary.get('num_successful')} successful structures"
    )

    # CIF zip may be missing when materialization failed — use export route
    assets = ouro._keyed_output_assets(action)
    candidates = explorer._load_candidates(
        chemical_system=chemical_system,
        stable_phases=stable_phases,
        candidate_cifs_asset=assets.get("candidate_cifs"),
        crystal_systems=None,
    )
    candidates.sort(
        key=lambda c: (
            0
            if c.get("is_dynamically_stable")
            else 1
            if c.get("is_dynamically_stable") is None
            else 2,
            c.get("e_above_hull")
            if c.get("e_above_hull") is not None
            else float("inf"),
        )
    )
    # Prefer Fe-containing, small cells for magnetic eval
    fe_cands = [
        c
        for c in candidates
        if c.get("cif_content")
        and c.get("formula")
        and "Fe" in c["formula"]
        and c.get("formula") != "Fe"
    ]
    survivors = (fe_cands or [c for c in candidates if c.get("cif_content")])[
        : config.max_candidates_to_evaluate
    ]

    materials = []
    for cand in survivors:
        material = explorer._candidate_to_material(cand, chemical_system)
        if material is not None:
            materials.append(material)

    exp_summary = explorer._build_summary(
        summary, candidates, materials, chemical_system
    )
    logger.info(
        f"{chemical_system}: {exp_summary.num_near_hull} near-hull → "
        f"{len(materials)} to evaluate"
    )

    evaluator = MaterialEvaluator(
        ouro,
        registry,
        # Smoke stays on fast tier-1 routes; MAE is a multi-hour DFT job.
        tier2_thresholds=Tier2Thresholds(magnetic_density_min=1e9),
    )
    scorer = MaterialScorer(config.scoring_weights)
    hypothesis = "Fe-Al smoke test against hosted GGen."

    candidate_results = []
    for material in materials:
        try:
            props = evaluator.evaluate_properties(material)
        except Exception as e:
            logger.warning(f"Eval failed for {material.composition}: {e}")
            props = MaterialProperties(
                space_group=material.resolved_space_group,
                num_atoms=material.num_atoms,
                e_hull=(material.predicted_properties or {}).get("e_hull"),
                dynamic_stability=(material.predicted_properties or {}).get(
                    "dynamic_stability"
                ),
                evaluation_errors={"complete_failure": str(e)},
            )
        score = scorer.calculate_score(material, props, config.default_targets)
        candidate_results.append(
            {
                "iteration": 0,
                "hypothesis": hypothesis,
                "composition": material.composition,
                "chemical_system": material.chemical_system,
                "num_atoms": material.num_atoms,
                "space_group_resolved": material.resolved_space_group,
                "generation_method": material.generation_method,
                "structure_file": material.file,
                "artifacts": material.artifacts,
                "properties": props.__dict__,
                "score": score,
                "material_id": material.material_id,
                "insights": "",
            }
        )
        logger.info(f"  {material.composition} score={score:.3f}")

    best = max(candidate_results, key=lambda c: c["score"], default=None)
    discovery = {
        "best_material": best,
        "all_results": candidate_results,
        "iterations_run": 1,
        "exploration_history": [exp_summary.to_dict()],
        "explored_systems": [exp_summary.chemical_system],
    }

    logger.info("=" * 70)
    if best:
        logger.info(f"BEST: {best['composition']} score={best['score']:.3f}")
        logger.info(
            f"  props={json.dumps(best.get('properties'), default=str)[:500]}"
        )

    try:
        publisher.publish_run_summary(
            discovery=discovery,
            targets=config.default_targets,
            run_title="Scientist smoke test (hosted GGen)",
            post_id=post_id,
        )
        logger.info(f"Published summary to post {post_id}")
    except Exception as e:
        logger.exception(f"Publish failed: {e}")

    print(
        json.dumps(
            {
                "post_id": post_id,
                "explore_action": action_id,
                "explored_systems": discovery.get("explored_systems"),
                "n_candidates": len(candidate_results),
                "best": (
                    {
                        "composition": best["composition"],
                        "score": best["score"],
                        "chemical_system": best.get("chemical_system"),
                    }
                    if best
                    else None
                ),
                "exploration_history": discovery.get("exploration_history"),
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
