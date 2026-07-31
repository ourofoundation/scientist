"""Main AI Scientist module for material discovery."""

import dspy
import json
from typing import List, Dict, Optional, Any

from ..agents.signatures import (
    AnalyzeMagnetLandscape,
    ProposeChemicalSystem,
    InterpretExplorationResults,
    RefineHypothesis,
)
from ..data.models import Material, MaterialProperties, ExplorationSummary
from ..computational.tools import ComputationalTools
from ..computational.evaluator import MaterialEvaluator, Interpretation
from ..computational.explorer import SystemExplorer
from ..computational.scorer import MaterialScorer
from ..utils.logging import get_logger
from .config import ScientistConfig

logger = get_logger("scientist")


class MaterialDiscoveryScientist(dspy.Module):
    """AI Scientist for discovering rare-earth-free permanent magnets.

    Loop:
      1. LLM proposes a chemical system + constraints
      2. Hosted GGen explores it in bulk (stoichiometries → MLIP-relaxed structures → hull)
      3. Near-hull survivors are evaluated on Ouro for magnetic/cost properties
      4. LLM interprets results and refines the next system proposal
    """

    def __init__(self, config: ScientistConfig, post_id: Optional[str] = None):
        super().__init__()
        self.config = config
        self.post_id = post_id

        self.analyze_landscape = dspy.ChainOfThought(AnalyzeMagnetLandscape)
        self.propose_system = dspy.ChainOfThought(ProposeChemicalSystem)
        self.interpret_exploration = dspy.Predict(InterpretExplorationResults)
        self.refine_hypothesis = dspy.Predict(RefineHypothesis)

        self.tools = ComputationalTools(config, post_id=post_id)
        self.evaluator = MaterialEvaluator(
            ouro_client=self.tools.ouro_client,
            registry=self.tools.material_registry,
        )
        self.scorer = MaterialScorer(config.scoring_weights)

        self.discovery_history: List[Dict] = []
        self.best_materials: List[Dict] = []
        self.exploration_history: List[ExplorationSummary] = []
        self.explored_systems: List[str] = []

    def forward(self, target_properties: Dict) -> Dict:
        """Main discovery loop over chemical systems."""
        landscape = self.analyze_landscape(
            constraints="No rare-earth elements; synthesizable permanent magnets",
            target_properties=json.dumps(target_properties),
            prior_explorations="none",
        )
        logger.info(f"Landscape analysis: {landscape.analysis}")
        logger.info(f"Promising directions: {landscape.promising_directions}")

        all_results: List[Dict] = []
        current_hypothesis: Optional[str] = None
        best_score = 0.0
        current_best: Optional[Dict] = None

        for iteration in range(self.config.max_iterations):
            result = self._run_iteration(
                iteration=iteration,
                target_properties=target_properties,
                landscape=landscape,
                guiding_hypothesis=current_hypothesis,
            )

            all_results.extend(result["candidate_results"])
            current_hypothesis = result["hypothesis"]
            self.exploration_history.append(result["summary"])
            self.explored_systems.append(result["chemical_system"])

            for cand in result["candidate_results"]:
                if cand["score"] > best_score:
                    best_score = cand["score"]
                    current_best = cand
                    self.best_materials.append(cand)

            self._log_iteration(result)

            if best_score > self.config.early_stopping_threshold:
                logger.info(
                    f"Excellent material found at iteration {iteration} "
                    f"(score={best_score:.3f})"
                )
                break

            # Refine hypothesis for next round
            if iteration < self.config.max_iterations - 1:
                current_hypothesis = self._refine_hypothesis(
                    current_hypothesis, iteration + 1
                )

        return self._build_discovery_result(all_results, current_best)

    def _run_iteration(
        self,
        iteration: int,
        target_properties: Dict,
        landscape: Any,
        guiding_hypothesis: Optional[str],
    ) -> Dict:
        """Propose → explore → evaluate → interpret one chemical system."""
        proposal = self.propose_system(
            landscape_analysis=landscape.analysis,
            previous_explorations=json.dumps(
                [s.to_dict() for s in self.exploration_history]
            ),
            guiding_hypothesis=guiding_hypothesis or "none",
            target_properties=json.dumps(target_properties),
            explored_systems=", ".join(self.explored_systems) or "none",
        )

        chemical_system = proposal.chemical_system.strip()
        hypothesis = proposal.hypothesis

        crystal_systems = SystemExplorer.parse_crystal_systems(
            getattr(proposal, "crystal_systems", "all")
        )
        min_fraction = SystemExplorer.parse_fraction_json(
            getattr(proposal, "min_fraction", "{}")
        )
        max_fraction = SystemExplorer.parse_fraction_json(
            getattr(proposal, "max_fraction", "{}")
        )

        logger.info(
            f"Iteration {iteration}: exploring {chemical_system} "
            f"(crystal_systems={crystal_systems or 'all'})"
        )
        logger.info(f"Hypothesis: {hypothesis}")

        materials, summary = self.tools.explore_system(
            chemical_system=chemical_system,
            crystal_systems=crystal_systems,
            min_fraction=min_fraction,
            max_fraction=max_fraction,
        )
        summary.hypothesis = hypothesis

        # Evaluate survivors on Ouro
        candidate_results: List[Dict] = []
        for material in materials:
            try:
                props = self.evaluator.evaluate_properties(material)
            except Exception as e:
                logger.warning(f"Evaluation failed for {material.composition}: {e}")
                props = MaterialProperties(
                    space_group=material.resolved_space_group,
                    num_atoms=material.num_atoms,
                    e_hull=(material.predicted_properties or {}).get("e_hull"),
                    dynamic_stability=(material.predicted_properties or {}).get(
                        "dynamic_stability"
                    ),
                    evaluation_errors={"complete_failure": str(e)},
                )
                material.predicted_properties = props.__dict__

            score = self.scorer.calculate_score(material, props, target_properties)
            candidate_results.append(
                self._record_candidate(
                    iteration=iteration,
                    material=material,
                    props=props,
                    hypothesis=hypothesis,
                    score=score,
                    rationale=getattr(proposal, "rationale", ""),
                )
            )

        # Interpret the exploration as a whole
        interpretation = self._interpret(
            chemical_system=chemical_system,
            hypothesis=hypothesis,
            summary=summary,
            candidates=candidate_results,
            targets=target_properties,
        )
        summary.insights = interpretation.insights

        for cand in candidate_results:
            cand["insights"] = interpretation.insights
            cand["analysis"] = interpretation.analysis

        return {
            "chemical_system": chemical_system,
            "hypothesis": hypothesis,
            "summary": summary,
            "candidate_results": candidate_results,
            "interpretation": interpretation,
            "rationale": getattr(proposal, "rationale", ""),
        }

    def _interpret(
        self,
        chemical_system: str,
        hypothesis: str,
        summary: ExplorationSummary,
        candidates: List[Dict],
        targets: Dict,
    ) -> Interpretation:
        try:
            result = self.interpret_exploration(
                chemical_system=chemical_system,
                hypothesis=hypothesis,
                exploration_summary=json.dumps(summary.to_dict()),
                evaluated_candidates=json.dumps(
                    [
                        {
                            "composition": c["composition"],
                            "score": c["score"],
                            "properties": c["properties"],
                            "space_group": c.get("space_group_resolved"),
                        }
                        for c in candidates
                    ]
                ),
                target_properties=json.dumps(targets),
            )
            return Interpretation(
                analysis=getattr(result, "analysis", ""),
                insights=getattr(result, "insights", ""),
            )
        except Exception as e:
            logger.warning(f"Interpretation failed: {e}")
            return Interpretation(
                analysis=f"Interpretation failed: {e}",
                insights=f"Explored {chemical_system}; {len(candidates)} evaluated.",
            )

    def _refine_hypothesis(
        self, current_hypothesis: Optional[str], iteration: int
    ) -> str:
        if not current_hypothesis:
            return ""
        top = sorted(self.best_materials, key=lambda r: r["score"], reverse=True)[:5]
        refinement = self.refine_hypothesis(
            original_hypothesis=current_hypothesis,
            exploration_history=json.dumps(
                [s.to_dict() for s in self.exploration_history]
            ),
            best_results=json.dumps(
                [
                    {
                        "composition": r["composition"],
                        "score": r["score"],
                        "chemical_system": r.get("chemical_system"),
                        "properties": r.get("properties"),
                    }
                    for r in top
                ]
            ),
            iteration=str(iteration),
        )
        return refinement.refined_hypothesis

    def _record_candidate(
        self,
        iteration: int,
        material: Material,
        props: MaterialProperties,
        hypothesis: str,
        score: float,
        rationale: str,
    ) -> Dict:
        return {
            "iteration": iteration,
            "hypothesis": hypothesis,
            "composition": material.composition,
            "chemical_system": material.chemical_system,
            "num_atoms": material.num_atoms,
            "space_group_requested": material.requested_space_group,
            "space_group_used": material.used_space_group,
            "space_group_resolved": material.resolved_space_group,
            "generation_method": material.generation_method,
            "structure_file": material.file,
            "artifacts": material.artifacts,
            "properties": props.__dict__,
            "score": score,
            "material_id": material.material_id,
            "strategy_rationale": rationale,
            "insights": "",
            "analysis": "",
        }

    def _log_iteration(self, result: Dict) -> None:
        system = result["chemical_system"]
        summary: ExplorationSummary = result["summary"]
        candidates = result["candidate_results"]
        best = max((c["score"] for c in candidates), default=0.0)
        logger.info(
            f"{system}: {summary.num_near_hull} near-hull → "
            f"{len(candidates)} evaluated, best score={best:.3f}"
        )
        for c in sorted(candidates, key=lambda x: x["score"], reverse=True)[:3]:
            logger.info(
                f"  {c['composition']} SG={c.get('space_group_resolved')} "
                f"score={c['score']:.3f}"
            )
        logger.info("-" * 70)

    def _build_discovery_result(
        self, all_results: List[Dict], current_best: Optional[Dict]
    ) -> Dict:
        return {
            "best_material": current_best or (
                self.best_materials[-1] if self.best_materials else None
            ),
            "all_results": all_results,
            "iterations_run": len(self.exploration_history),
            "exploration_history": [s.to_dict() for s in self.exploration_history],
            "explored_systems": list(self.explored_systems),
        }
