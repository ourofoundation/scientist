"""Main AI Scientist module for material discovery."""

import dspy
import json
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass, field

from ..agents.signatures import (
    AnalyzeMagnetLandscape,
    GenerateMagnetHypothesis,
    DesignMaterialCandidate,
    InterpretSimulationResults,
    RefineHypothesis,
    GenerateMutationOperations,
    DecideGenerationMode,
)
from ..data.models import Material, MaterialProperties, MutationRecord
from ..computational.tools import ComputationalTools
from ..computational.evaluator import MaterialEvaluator
from ..computational.scorer import MaterialScorer
from ..utils.logging import get_logger
from .config import ScientistConfig

logger = get_logger("scientist")


class MaterialDiscoveryScientist(dspy.Module):
    """AI Scientist for discovering rare-earth-free permanent magnets."""

    def __init__(self, config: ScientistConfig, post_id: Optional[str] = None):
        """Initialize the AI Scientist.

        Args:
            config: Configuration object with all settings
            post_id: Optional Ouro post ID for asset parenting
        """
        super().__init__()
        self.config = config
        self.post_id = post_id

        # Initialize DSPy modules
        self.analyze_landscape = dspy.ChainOfThought(AnalyzeMagnetLandscape)
        self.generate_hypothesis = dspy.ChainOfThought(GenerateMagnetHypothesis)
        self.design_material = dspy.Predict(DesignMaterialCandidate)
        self.interpret_results = dspy.Predict(InterpretSimulationResults)
        self.refine_hypothesis = dspy.Predict(RefineHypothesis)
        self.generate_mutation = dspy.ChainOfThought(GenerateMutationOperations)
        self.decide_mode = dspy.ChainOfThought(DecideGenerationMode)

        # Initialize computational tools
        self.tools = ComputationalTools(config, post_id=post_id)
        self.evaluator = MaterialEvaluator(
            ouro_client=self.tools.ouro_client,
            registry=self.tools.material_registry,
        )
        self.scorer = MaterialScorer(config.scoring_weights)

        # Memory of discoveries and mutations
        self.discovery_history = []
        self.best_materials = []
        self.mutation_success_rates = {}

    @dataclass
    class Operation:
        """Represents a single mutation operation."""

        op: str
        params: Dict[str, Any] = field(default_factory=dict)

    @dataclass
    class ModeDecision:
        """Represents a mode decision (new vs mutate)."""

        decision: str
        target_material_id: Optional[str]
        rationale: str = ""

    def forward(self, target_properties: Dict) -> Dict:
        """Main discovery loop.

        Args:
            target_properties: Target properties for materials

        Returns:
            Discovery results including best material and statistics
        """
        # Initial landscape analysis
        landscape = self.analyze_landscape(
            constraints="No rare-earth elements",
            target_properties=json.dumps(target_properties),
        )

        logger.info(f"Landscape analysis: {landscape.analysis}")
        logger.info(f"Promising directions: {landscape.promising_directions}")

        # Track results across iterations
        all_results = []
        current_hypothesis = None
        best_score = 0
        current_best_material = None

        for iteration in range(self.config.max_iterations):
            result = self._run_iteration(
                iteration=iteration,
                target_properties=target_properties,
                landscape=landscape,
                all_results=all_results,
                current_hypothesis=current_hypothesis,
                current_best_material=current_best_material,
            )

            all_results.append(result["result"])
            current_hypothesis = result["hypothesis"]

            if result["score"] > best_score:
                best_score = result["score"]
                current_best_material = result["material"]
                self.best_materials.append(result["result"])

            # Update mutation stats
            self._update_mutation_stats(
                result["material"], target_properties, result["score"]
            )

            # Early stopping
            if result["score"] > self.config.early_stopping_threshold:
                logger.info(f"Excellent material found at iteration {iteration}!")
                break

            self._log_iteration_result(result["material"], result["score"], iteration)

        return self._build_discovery_result(all_results)

    def _run_iteration(
        self,
        iteration: int,
        target_properties: Dict,
        landscape: Any,
        all_results: List,
        current_hypothesis: Optional[str],
        current_best_material: Optional[Material],
    ) -> Dict:
        """Run a single discovery iteration.

        Returns:
            Dict with result, material, score, and hypothesis
        """
        # Decide generation mode
        decision_output = self._decide_mode(
            iteration, all_results, current_best_material, target_properties
        )
        mode_decision = self._to_mode_decision(decision_output)
        mode = mode_decision.decision
        target_material_id = mode_decision.target_material_id

        logger.info(f"Iteration {iteration}: mode={mode}, target={target_material_id}")

        # Plan mutations if needed
        operations = []
        strategy_output = decision_output
        if mode == "mutate":
            material = self.tools.material_registry.get(target_material_id)
            logger.debug(f"Mutating: {material}")
            mutation_history = self.tools.get_mutation_history_summary()
            strategy_output = self.generate_mutation(
                iteration=iteration,
                material=material.to_json(),
                target_properties=json.dumps(target_properties),
                mutation_history=json.dumps(mutation_history),
            )
            operations = strategy_output.get("operations", [])

        # Generate/refine hypothesis
        if iteration == 0:
            hypothesis_gen = self.generate_hypothesis(
                previous_results=[],
                landscape_analysis=landscape.analysis,
                design_strategy=mode,
            )
            hypothesis = hypothesis_gen.hypothesis
        else:
            hypothesis = self._refine_current_hypothesis(
                current_hypothesis, all_results[-1], iteration
            )

        # Generate material
        material = self._generate_material(
            mode, target_material_id, operations, hypothesis, current_best_material
        )

        # Evaluate and interpret
        try:
            material_props, interpretation = self._evaluate_and_interpret(
                material, target_properties
            )
        except Exception as e:
            logger.warning(f"Evaluation failed for {material.composition}: {e}")
            material_props = MaterialProperties(
                space_group=material.resolved_space_group,
                num_atoms=material.num_atoms,
                evaluation_errors={"complete_failure": str(e)},
            )
            material.predicted_properties = material_props.__dict__
            interpretation = type(
                "Interpretation", (), {"insights": f"Evaluation failed: {e}"}
            )()

        logger.debug(f"Properties: {material_props}")

        if not material_props.has_minimum_properties():
            logger.warning(f"Insufficient properties for {material.composition}")

        # Score and record
        score = self.scorer.calculate_score(material, material_props, target_properties)
        result = self._record_result(
            iteration,
            material,
            material_props,
            interpretation,
            hypothesis,
            mode,
            strategy_output,
            score,
        )

        if material_props.evaluation_errors:
            result["evaluation_errors"] = material_props.evaluation_errors

        return {
            "result": result,
            "material": material,
            "score": score,
            "hypothesis": hypothesis,
        }

    def _to_mode_decision(self, decision_output: Any) -> ModeDecision:
        """Validate and convert DecideGenerationMode output."""
        decision = str(getattr(decision_output, "decision", "new")).lower()
        if decision not in {"new", "mutate"}:
            decision = "new"
        tmid = getattr(decision_output, "target_material_id", None)
        if not isinstance(tmid, str) or not tmid.strip():
            tmid = None
        rationale = getattr(decision_output, "rationale", "")
        return self.ModeDecision(
            decision=decision, target_material_id=tmid, rationale=rationale
        )

    def _decide_mode(
        self,
        iteration: int,
        all_results: List,
        current_best_material: Optional[Material],
        target_properties: Dict,
    ) -> Any:
        """Call DecideGenerationMode with prepared context."""
        available_materials = [
            {
                "id": r["material_id"],
                "composition": r["composition"],
                "score": r["score"],
                "properties": r.get("properties", {}),
                "is_current_best": (
                    current_best_material is not None
                    and r["material_id"] == current_best_material.material_id
                ),
            }
            for r in all_results
        ]

        current_material_desc = (
            f"{current_best_material.composition} with properties "
            f"{current_best_material.predicted_properties}"
            if current_best_material
            else "none"
        )

        return self.decide_mode(
            iteration=iteration,
            current_material=current_material_desc,
            target_properties=json.dumps(target_properties),
            mutation_history=json.dumps(self.tools.get_mutation_history_summary()),
            available_materials=json.dumps(available_materials),
        )

    def _refine_current_hypothesis(
        self, current_hypothesis: str, last_result: Dict, iteration: int
    ) -> str:
        """Refine hypothesis based on last result."""
        refinement = self.refine_hypothesis(
            original_hypothesis=current_hypothesis,
            results=json.dumps(
                {
                    **last_result["properties"],
                    "space_group_requested": last_result.get("space_group_requested"),
                    "space_group_used": last_result.get("space_group_used"),
                    "space_group_resolved": last_result.get("space_group_resolved"),
                }
            ),
            insights=last_result["insights"],
            iteration=str(iteration),
            mutation_history=json.dumps(self.tools.get_mutation_history_summary()),
        )
        return refinement.refined_hypothesis

    def _generate_material(
        self,
        mode: str,
        target_material_id: Optional[str],
        operations: List[Dict[str, Any]],
        current_hypothesis: str,
        current_best_material: Optional[Material],
    ) -> Material:
        """Generate material based on mode and parameters."""
        if mode == "mutate" and target_material_id:
            target_material = self.tools.material_registry.get(target_material_id)
            if target_material is None:
                logger.warning(
                    f"Target {target_material_id} not found, using new generation"
                )
            else:
                logger.info(
                    f"Mutating {target_material.composition} with {len(operations)} ops"
                )
                if not operations:
                    logger.debug("No operations provided, using default jitter")
                    operations = [{"op": "jitter_sites", "sigma": 0.01}]
                return self.tools.mutate_material(target_material, operations)

        # New generation
        material_design = self.design_material(
            hypothesis=current_hypothesis,
            constraints="No rare earths, synthesizable via standard methods, less than 20 atoms",
            compatible_space_groups=[],
        )

        compatible_sgs = self.tools.get_compatible_space_groups(
            material_design.composition
        )

        material_design = self.design_material(
            hypothesis=current_hypothesis,
            constraints="No rare earths, synthesizable via standard methods, less than 20 atoms",
            compatible_space_groups=compatible_sgs[:20],
        )

        logger.debug(
            f"Design: {material_design.composition} SG {material_design.space_group}"
        )

        return self.tools.generate_structure(
            composition=material_design.composition,
            space_group=material_design.space_group,
        )

    def _evaluate_and_interpret(
        self, material: Material, targets: Dict
    ) -> Tuple[MaterialProperties, Any]:
        """Evaluate material properties and interpret results."""
        material_props = self.evaluator.evaluate_properties(material)
        material.predicted_properties = material_props.__dict__

        interpretation = self.evaluator.interpret_results(
            material, material_props, targets, self.interpret_results
        )
        return material_props, interpretation

    def _record_result(
        self,
        iteration: int,
        material: Material,
        material_props: MaterialProperties,
        interpretation: Any,
        hypothesis: str,
        mode: str,
        strategy_output: Any,
        score: float,
    ) -> Dict:
        """Record a discovery result."""
        rationale = getattr(strategy_output, "rationale", "N/A")
        return {
            "iteration": iteration,
            "hypothesis": hypothesis,
            "composition": material.composition,
            "num_atoms": material.num_atoms,
            "space_group_requested": material.requested_space_group,
            "space_group_used": material.used_space_group,
            "space_group_resolved": material.resolved_space_group,
            "generation_method": material.generation_method,
            "parent_material_id": material.parent_material_id,
            "mutation_history": [m.__dict__ for m in material.mutation_history],
            "structure_file": material.file,
            "artifacts": material.artifacts,
            "properties": material_props.__dict__,
            "insights": interpretation.insights,
            "score": score,
            "material_id": material.material_id,
            "strategy_mode": mode,
            "strategy_rationale": rationale,
        }

    def _update_mutation_stats(
        self, material: Material, targets: Dict, score: float
    ) -> None:
        """Update mutation success statistics."""
        if material.generation_method != "mutation" or not material.mutation_history:
            return

        last_mutation = material.mutation_history[-1]
        if material.parent_material_id not in self.tools.material_registry:
            return

        parent = self.tools.material_registry[material.parent_material_id]
        if not parent.predicted_properties:
            parent_props = self.tools.evaluate_material_properties(parent)
            parent.predicted_properties = parent_props.__dict__
        else:
            parent_props = MaterialProperties(**parent.predicted_properties)

        parent_score = self.scorer.calculate_score(parent, parent_props, targets)

        if last_mutation.property_changes is None:
            last_mutation.property_changes = {}
        last_mutation.property_changes["score_change"] = score - parent_score

        mutation_type = last_mutation.mutation_type
        if mutation_type not in self.mutation_success_rates:
            self.mutation_success_rates[mutation_type] = {"successes": 0, "total": 0}

        self.mutation_success_rates[mutation_type]["total"] += 1
        if score > parent_score:
            self.mutation_success_rates[mutation_type]["successes"] += 1

    def _log_iteration_result(
        self, material: Material, score: float, iteration: int
    ) -> None:
        """Log iteration result."""
        suffix = (
            f" (mutated from {material.parent_material_id})"
            if material.generation_method == "mutation"
            else ""
        )
        logger.info(
            f"Iter {iteration}: {material.composition} - Score: {score:.3f}{suffix}"
        )
        logger.info("-" * 70)

    def _build_discovery_result(self, all_results: List) -> Dict:
        """Build final discovery result dictionary."""
        mutation_summary = {}
        for mutation_type, stats in self.mutation_success_rates.items():
            success_rate = (
                stats["successes"] / stats["total"] if stats["total"] > 0 else 0
            )
            mutation_summary[mutation_type] = {
                "success_rate": success_rate,
                "total_attempts": stats["total"],
            }

        return {
            "best_material": self.best_materials[-1] if self.best_materials else None,
            "all_results": all_results,
            "iterations_run": len(all_results),
            "mutation_summary": mutation_summary,
            "mutation_history": self.tools.get_mutation_history_summary(),
            "trajectory_visualization": self.tools.get_trajectory_visualization(),
        }
