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
from .config import ScientistConfig


class MaterialDiscoveryScientist(dspy.Module):
    """AI Scientist for discovering rare-earth-free permanent magnets."""

    def __init__(self, config: ScientistConfig, post_id: Optional[str] = None):
        """Initialize the AI Scientist.

        Args:
            config: Configuration object with all settings
        """
        super().__init__()
        self.config = config
        # Ouro run post id for parenting assets
        self.post_id: Optional[str] = post_id

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
        self.evaluator = MaterialEvaluator(self.tools)
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
    class StrategyDecision:
        """Represents a strategy decision for material generation."""

        mode: str
        target_material_id: Optional[str]
        operations: List["MaterialDiscoveryScientist.Operation"]
        rationale: str = ""

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
        # Normalize targets
        # target_properties = self._normalize_targets(target_properties)

        # Initial landscape analysis
        landscape = self.analyze_landscape(
            constraints="No rare-earth elements",
            target_properties=json.dumps(target_properties),
        )

        print(f"Landscape analysis: {landscape.analysis}")
        print(f"Promising directions: {landscape.promising_directions}")

        # Track results across iterations
        all_results = []
        current_hypothesis = None
        best_score = 0
        current_best_material = None

        for iteration in range(self.config.max_iterations):
            # Decide mode first (new vs mutate)
            decision_output = self._decide_mode(
                iteration=iteration,
                all_results=all_results,
                current_best_material=current_best_material,
                target_properties=target_properties,
            )
            mode_decision = self._to_mode_decision(decision_output)
            mode, target_material_id = (
                mode_decision.decision,
                mode_decision.target_material_id,
            )
            print(
                f"Iteration: {iteration}, generation mode: {mode}, target: {target_material_id}"
            )

            # If mutate, plan operations
            operations: List[Dict[str, Any]] = []
            strategy_output = decision_output
            if mode == "mutate":
                material = self.tools.material_registry.get(target_material_id)
                print(f"Material to mutate: {material}")
                mutation_history = self.tools.get_mutation_history_summary()
                print(f"Mutation history: {mutation_history}")
                strategy_output = self.generate_mutation(
                    iteration=iteration,
                    material=material.to_json(),
                    target_properties=json.dumps(target_properties),
                    mutation_history=json.dumps(mutation_history),
                )

            # Generate/refine hypothesis
            if iteration == 0:
                hypothesis_gen = self.generate_hypothesis(
                    previous_results=[],
                    landscape_analysis=landscape.analysis,
                    design_strategy=mode,
                )
                current_hypothesis = hypothesis_gen.hypothesis
            else:
                current_hypothesis = self._refine_current_hypothesis(
                    current_hypothesis, all_results[-1], iteration
                )

            operations = strategy_output.get("operations", [])
            # Generate material according to decision
            material = self._generate_material(
                mode=mode,
                target_material_id=target_material_id,
                operations=operations,
                current_hypothesis=current_hypothesis,
                current_best_material=current_best_material,
            )

            # Evaluate and interpret
            material_props, interpretation = self._evaluate_and_interpret(
                material, target_properties
            )

            print(f"Material properties: {material_props}")
            print(f"Interpretation: {interpretation}")

            # Score and record
            score = self.scorer.calculate_score(
                material, material_props, target_properties
            )
            result = self._record_result(
                iteration=iteration,
                material=material,
                material_props=material_props,
                interpretation=interpretation,
                hypothesis=current_hypothesis,
                mode=mode,
                strategy_output=strategy_output,
                score=score,
            )
            all_results.append(result)

            # Track best
            if score > best_score:
                best_score = score
                current_best_material = material
                self.best_materials.append(result)

            # Update mutation stats
            self._update_mutation_stats(material, target_properties, score)

            # Early stopping
            if score > self.config.early_stopping_threshold:
                print(f"Excellent material found at iteration {iteration}!")
                break

            suffix = (
                f" (mutated from {material.parent_material_id})"
                if material.generation_method == "mutation"
                else ""
            )
            print(
                f"Iteration {iteration}: {material.composition} - Score: {score:.3f}{suffix}"
            )
            print("-" * 70)

        # Generate final summary with mutation insights
        mutation_summary = {}
        for mutation_type, stats in self.mutation_success_rates.items():
            success_rate = (
                stats["successes"] / stats["total"] if stats["total"] > 0 else 0
            )
            mutation_summary[mutation_type] = {
                "success_rate": success_rate,
                "total_attempts": stats["total"],
            }

        # Generate trajectory visualization if available
        trajectory_visualization = self.tools.get_trajectory_visualization()

        return {
            "best_material": self.best_materials[-1] if self.best_materials else None,
            "all_results": all_results,
            "iterations_run": len(all_results),
            "mutation_summary": mutation_summary,
            "mutation_history": self.tools.get_mutation_history_summary(),
            "trajectory_visualization": trajectory_visualization,
        }

    def _normalize_targets(self, targets: Dict) -> Dict:
        """Normalize various target key aliases to a canonical schema."""
        if not isinstance(targets, dict):
            return {}
        alias_map = {
            "Tc_min": "curie_temperature_min",
            "curie_temp_min": "curie_temperature_min",
            "curie_temperature_min": "curie_temperature_min",
            "Ms_min": "magnetic_density_min",
            "magnetic_density_min": "magnetic_density_min",
            "e_hull_max": "e_hull_max",
            "cost_max": "cost_max",
            "num_atoms_max": "num_atoms_max",
            "BHmax_min": "BHmax_min",  # not used in score yet
            "Hc_min": "Hc_min",  # not used in score yet
        }
        normalized = {}
        for k, v in targets.items():
            key = alias_map.get(k, k)
            normalized[key] = v
        return normalized

    def _to_strategy_decision(
        self, strategy_output: Any
    ) -> "MaterialDiscoveryScientist.StrategyDecision":
        """Convert DSPy strategy JSON into a typed StrategyDecision."""
        mode = "new"
        target_material_id: Optional[str] = None
        operations: List[MaterialDiscoveryScientist.Operation] = []
        rationale = ""
        try:
            decision_raw = getattr(strategy_output, "strategy_decision", "{}")
            rationale = getattr(strategy_output, "rationale", "")
            decision = json.loads(decision_raw) if isinstance(decision_raw, str) else {}
            if isinstance(decision, dict):
                m = str(decision.get("mode", "new")).lower()
                if m in {"new", "mutate"}:
                    mode = m
                tmid = decision.get("target_material_id")
                if isinstance(tmid, str) and tmid.strip():
                    target_material_id = tmid
                ops = decision.get("operations", [])
                if isinstance(ops, list):
                    for op in ops:
                        if isinstance(op, dict) and op.get("op"):
                            op_name = op.get("op")
                            params = {k: v for k, v in op.items() if k != "op"}
                            operations.append(self.Operation(op=op_name, params=params))
        except Exception:
            print("⚠️  Failed to parse strategy decision; defaulting to new generation")

        return self.StrategyDecision(
            mode=mode,
            target_material_id=target_material_id,
            operations=operations,
            rationale=rationale,
        )

    def _to_mode_decision(
        self, decision_output: Any
    ) -> "MaterialDiscoveryScientist.ModeDecision":
        """Validate and convert DecideGenerationMode output to typed ModeDecision."""
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
        available_materials = []

        for result in all_results:
            available_materials.append(
                {
                    "id": result["material_id"],
                    "composition": result["composition"],
                    "score": result["score"],
                    "properties": result.get("properties", {}),
                    "is_current_best": (
                        True
                        if current_best_material
                        and result["material_id"] == current_best_material.material_id
                        else False
                    ),
                }
            )

        current_material_desc = (
            f"{current_best_material.composition} with properties {current_best_material.predicted_properties}"
            if current_best_material
            else "none"
        )
        mutation_history_json = json.dumps(self.tools.get_mutation_history_summary())
        available_materials_json = json.dumps(available_materials)

        return self.decide_mode(
            iteration=iteration,
            current_material=current_material_desc,
            target_properties=json.dumps(target_properties),
            mutation_history=mutation_history_json,
            available_materials=available_materials_json,
        )

    def _refine_current_hypothesis(
        self, current_hypothesis: str, last_result: Dict, iteration: int
    ) -> str:
        """Refine hypothesis based on last result."""
        mutation_history_json = json.dumps(self.tools.get_mutation_history_summary())
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
            mutation_history=mutation_history_json,
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
                print(f"Target material {target_material_id} not found")
                mode = "new"
            else:
                print(
                    f"Mutating material {target_material.composition} with operations {operations}"
                )
                if not operations:
                    print("No operations provided for mutation")
                    operations = [{"op": "jitter_sites", "sigma": 0.01}]
                mat = self.tools.mutate_material(
                    material=target_material,
                    operations=operations,
                )
                return mat

        # Default: new generation
        parent_material_desc = (
            f"{current_best_material.composition}" if current_best_material else "none"
        )
        # Part 1, just get the composition
        material_design = self.design_material(
            hypothesis=current_hypothesis,
            constraints="No rare earths, synthesizable via standard methods, less than 20 atoms",
            compatible_space_groups=[],
        )
        compatible_space_groups = self.tools.get_compatible_space_groups(
            material_design.composition
        )
        # Part 2, get the composition and space group
        material_design = self.design_material(
            hypothesis=current_hypothesis,
            constraints="No rare earths, synthesizable via standard methods, less than 20 atoms",
            compatible_space_groups=compatible_space_groups[:20],
        )
        print(f"Material design: {material_design}")
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
        try:
            rationale = getattr(strategy_output, "rationale", "N/A")
        except Exception:
            rationale = "N/A"
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
        if material.generation_method == "mutation" and material.mutation_history:
            last_mutation = material.mutation_history[-1]
            if material.parent_material_id in self.tools.material_registry:
                parent_material = self.tools.material_registry[
                    material.parent_material_id
                ]
                if (
                    hasattr(parent_material, "predicted_properties")
                    and parent_material.predicted_properties
                ):
                    parent_props = MaterialProperties(
                        **parent_material.predicted_properties
                    )
                else:
                    parent_props = self.tools.evaluate_material_properties(
                        parent_material
                    )
                    parent_material.predicted_properties = parent_props.__dict__

                parent_score = self.scorer.calculate_score(
                    parent_material, parent_props, targets
                )
                if last_mutation.property_changes is None:
                    last_mutation.property_changes = {}
                last_mutation.property_changes["score_change"] = score - parent_score

                mutation_type = last_mutation.mutation_type
                if mutation_type not in self.mutation_success_rates:
                    self.mutation_success_rates[mutation_type] = {
                        "successes": 0,
                        "total": 0,
                    }
                self.mutation_success_rates[mutation_type]["total"] += 1
                if score > parent_score:
                    self.mutation_success_rates[mutation_type]["successes"] += 1
