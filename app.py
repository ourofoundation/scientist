"""
AI Scientist Agent for Rare-Earth-Free Permanent Magnet Discovery
Using DSPy for systematic hypothesis generation and refinement
"""

import dspy
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import numpy as np
from pymatgen.core.structure import Structure
from pymatgen.io.cif import CifParser
from io import StringIO
from enum import Enum
import json
import requests
import dotenv
import os
import mlflow
from ouro import Ouro
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
from tools import ComputationalTools
from publisher import Publisher
from models import Material, MaterialProperties

dotenv.load_dotenv(override=True)


# Enable autologging with all features
mlflow.dspy.autolog(
    log_compiles=True,  # Track optimization process
    log_evals=True,  # Track evaluation results
    log_traces_from_compile=True,  # Track program traces during optimization
    # Log traces from module executions
    log_traces=True,
)

# Configure MLflow tracking
mlflow.set_tracking_uri("http://127.0.0.1:5000")  # Use local MLflow server
mlflow.set_experiment("scientist-optimization")


class DesignStrategy(Enum):
    """Different approaches to designing magnetic materials"""

    RANDOM_GENERATION = "random_generation"
    CHANGE_SPACE_GROUP = "change_space_group"
    CHANGE_STOICHIOMETRY = "change_stoichiometry"
    CHANGE_CHEMISTRY = "change_chemistry"
    # MUTATION = "mutation"
    # NANOSTRUCTURING = "nanostructure_engineering"
    # PHASE_ENGINEERING = "phase_boundary_engineering"
    # STRAIN_ENGINEERING = "strain_induced_anisotropy"
    # INTERMETALLIC = "new_intermetallic_compounds"


# ============================================================================
# DSPy Signatures for Scientific Reasoning
# ============================================================================


class AnalyzeMagnetLandscape(dspy.Signature):
    """Analyze current knowledge of permanent magnets to identify opportunities."""

    known_materials = dspy.InputField(
        desc="list of known magnetic materials and their properties"
    )
    rare_earth_free_constraint = dspy.InputField(desc="must avoid rare earth elements")
    target_properties = dspy.InputField(
        desc="desired magnetic properties (Hc, Ms, Tc, BHmax)"
    )

    analysis = dspy.OutputField(desc="analysis of gaps and opportunities")
    promising_directions = dspy.OutputField(
        desc="list of promising research directions"
    )


class GenerateMagnetHypothesis(dspy.Signature):
    """Generate hypothesis for new permanent magnet material."""

    previous_results = dspy.InputField(desc="results from previous iterations")
    landscape_analysis = dspy.InputField(
        desc="analysis of magnetic materials landscape"
    )
    design_strategy = dspy.InputField(desc="selected design strategy")

    hypothesis = dspy.OutputField(
        desc="specific hypothesis about material composition/structure"
    )
    rationale = dspy.OutputField(desc="scientific reasoning behind hypothesis")
    expected_properties = dspy.OutputField(desc="predicted magnetic properties")
    key_risks = dspy.OutputField(desc="main risks or challenges")


class DesignMaterialCandidate(dspy.Signature):
    """Design specific material candidate based on hypothesis."""

    hypothesis = dspy.InputField(desc="scientific hypothesis")
    constraints = dspy.InputField(
        desc="no rare earth elements, must be synthesizable, should be less than 20 atoms"
    )
    design_strategy = dspy.InputField(
        desc="approach (random generation, mutation, etc)"
    )

    composition = dspy.OutputField(
        desc="chemical composition using whole value notation"
    )
    space_group = dspy.OutputField(desc="space group number 1-230")


class InterpretSimulationResults(dspy.Signature):
    """Interpret computational results and extract insights."""

    material = dspy.InputField(desc="material composition and structure")
    simulation_results = dspy.InputField(desc="MatterGen/DFT calculation results")
    target_properties = dspy.InputField(desc="desired magnetic properties")

    performance_analysis = dspy.OutputField(desc="how well material meets targets")
    key_insights = dspy.OutputField(desc="what we learned")
    limiting_factors = dspy.OutputField(desc="what limits performance")


class RefineHypothesis(dspy.Signature):
    """Refine hypothesis based on computational results."""

    original_hypothesis = dspy.InputField(desc="original hypothesis")
    results = dspy.InputField(desc="computational evaluation results")
    insights = dspy.InputField(desc="key insights from analysis")
    iteration = dspy.InputField(desc="current iteration number")

    refined_hypothesis = dspy.OutputField(desc="improved hypothesis")
    modifications = dspy.OutputField(desc="specific changes made")
    confidence_score = dspy.OutputField(desc="confidence in new hypothesis (0-1)")


# ============================================================================
# DSPy Modules for AI Scientist
# ============================================================================


class MagnetDiscoveryScientist(dspy.Module):
    """AI Scientist for discovering rare-earth-free permanent magnets"""

    def __init__(self, max_iterations: int = 10):
        super().__init__()
        self.max_iterations = max_iterations

        # DSPy modules for different reasoning steps
        self.analyze_landscape = dspy.ChainOfThought(AnalyzeMagnetLandscape)
        self.generate_hypothesis = dspy.ChainOfThought(GenerateMagnetHypothesis)
        self.design_material = dspy.ChainOfThought(DesignMaterialCandidate)
        self.interpret_results = dspy.ChainOfThought(InterpretSimulationResults)
        self.refine_hypothesis = dspy.ChainOfThought(RefineHypothesis)

        # Computational tools
        self.tools = ComputationalTools()

        # Memory of discoveries
        self.discovery_history = []
        self.best_materials = []

    def forward(self, target_properties: Dict, known_materials: List[str]) -> Dict:
        """Main discovery loop"""

        # Initial landscape analysis
        landscape = self.analyze_landscape(
            known_materials=str(known_materials),
            rare_earth_free_constraint="No La, Ce, Pr, Nd, Pm, Sm, Eu, Gd, Tb, Dy, Ho, Er, Tm, Yb, Lu",
            target_properties=json.dumps(target_properties),
        )

        print(f"Landscape analysis: {landscape.analysis}")
        print(f"Promising directions: {landscape.promising_directions}")

        # Track results across iterations
        all_results = []
        current_hypothesis = None
        best_score = 0

        for iteration in range(self.max_iterations):
            # Select design strategy based on previous results
            strategy = self._select_strategy(all_results)

            # Generate or refine hypothesis
            if iteration == 0:
                hypothesis_gen = self.generate_hypothesis(
                    previous_results="None - first iteration",
                    landscape_analysis=landscape.analysis,
                    design_strategy=strategy.value,
                )
                current_hypothesis = hypothesis_gen.hypothesis
                rationale = hypothesis_gen.rationale
            else:
                # Refine based on previous results
                last_result = all_results[-1]
                # Include SG feedback to the refinement step so the agent learns
                # when a requested SG was incompatible and what was ultimately used/resolved
                refinement = self.refine_hypothesis(
                    original_hypothesis=current_hypothesis,
                    results=json.dumps(
                        {
                            **last_result["properties"],
                            "space_group_requested": last_result.get(
                                "space_group_requested"
                            ),
                            "space_group_used": last_result.get("space_group_used"),
                            "space_group_resolved": last_result.get(
                                "space_group_resolved"
                            ),
                        }
                    ),
                    insights=last_result["insights"],
                    iteration=str(iteration),
                )
                current_hypothesis = refinement.refined_hypothesis

                # Check confidence
                if float(refinement.confidence_score) < 0.3:
                    # Switch to different approach
                    strategy = self._select_alternative_strategy(strategy)
                    hypothesis_gen = self.generate_hypothesis(
                        previous_results=json.dumps(all_results[-3:]),
                        landscape_analysis=landscape.analysis,
                        design_strategy=strategy.value,
                    )
                    current_hypothesis = hypothesis_gen.hypothesis

            # Design specific material
            material_design = self.design_material(
                hypothesis=current_hypothesis,
                constraints="No rare earths, synthesizable via standard methods, should be less than 20 atoms",
                design_strategy=strategy.value,
            )

            # Generate structure using computational tools
            material = self.tools.generate_structure(
                composition=material_design.composition,
                space_group=material_design.space_group,
                # constraints={"structure_type": material_design.structure_type},
            )

            # Evaluate properties
            material_props = self.tools.evaluate_material_properties(material)

            # Interpret results
            interpretation = self.interpret_results(
                material=(
                    f"{material.composition} with space group "
                    f"{material.resolved_space_group if material.resolved_space_group is not None else material.used_space_group}"
                ),
                simulation_results=json.dumps({"material": material_props.__dict__}),
                target_properties=json.dumps(target_properties),
            )

            # Calculate overall score
            score = self._calculate_score(material, material_props, target_properties)

            # Store results
            result = {
                "iteration": iteration,
                "hypothesis": current_hypothesis,
                "composition": material_design.composition,
                "num_atoms": material.num_atoms,
                "space_group_requested": material.requested_space_group,
                "space_group_used": material.used_space_group,
                "space_group_resolved": material.resolved_space_group,
                "structure_file": material.file,
                "artifacts": material.artifacts,
                "properties": material_props.__dict__,
                "insights": interpretation.key_insights,
                "score": score,
            }
            all_results.append(result)

            # Track best materials
            if score > best_score:
                best_score = score
                self.best_materials.append(result)

            # Early stopping if we found excellent material
            if score > 0.9:
                print(f"Excellent material found at iteration {iteration}!")
                break

            print(
                f"Iteration {iteration}: {material_design.composition} - Score: {score:.3f}"
            )
            print("-" * 70)

        return {
            "best_material": self.best_materials[-1] if self.best_materials else None,
            "all_results": all_results,
            "iterations_run": len(all_results),
        }

    def _select_strategy(self, previous_results: List) -> DesignStrategy:
        """Select design strategy based on previous results"""
        if not previous_results:
            print("No previous results, selecting random generation")
            return DesignStrategy.RANDOM_GENERATION

        # Analyze what strategies have been working
        strategy_scores = {}
        for strategy in DesignStrategy:
            strategy_scores[strategy] = 0

        # In real implementation, analyze which strategies led to high scores
        # For now, rotate through strategies
        iteration = len(previous_results)
        strategies = list(DesignStrategy)
        chosen = strategies[iteration % len(strategies)]
        print(f"Selecting strategy: {chosen}")
        return chosen

    def _select_alternative_strategy(self, current: DesignStrategy) -> DesignStrategy:
        """Select alternative strategy when current isn't working"""
        alternatives = [s for s in DesignStrategy if s != current]
        return np.random.choice(alternatives)

    def _calculate_score(
        self, material: Material, props: MaterialProperties, targets: Dict
    ) -> float:
        """Calculate overall material score"""
        score = 0.0
        weights = {
            "e_hull": 0.15,
            "cost": 0.15,
            "magnetic_density": 0.15,
            "curie_temperature": 0.15,
            "dynamic_stability": 0.1,
            "num_atoms": 0.1,
        }

        # Number of atoms
        if "num_atoms_max" in targets:
            score += weights["num_atoms"] * min(
                1.0, material.num_atoms / targets["num_atoms_max"]
            )

        # Energy above the hull
        if "e_hull_max" in targets:
            score += weights["e_hull"] * min(1.0, props.e_hull / targets["e_hull_max"])
        # Cost
        if "cost_max" in targets:
            score += weights["cost"] * min(1.0, props.cost / targets["cost_max"])
        # Magnetic density
        if "magnetic_density_min" in targets:
            score += weights["magnetic_density"] * min(
                1.0, props.magnetic_density / targets["magnetic_density_min"]
            )
        # Curie temperature (must be well above room temperature)
        if "curie_temperature_min" in targets:
            score += weights["curie_temperature"] * min(
                1.0, props.curie_temperature / targets["curie_temperature_min"]
            )
        # Dynamic stability
        score += weights["dynamic_stability"] * (
            1.0 if props.dynamic_stability else 0.3
        )

        return score


# ============================================================================
# Training Data Collection for DSPy Optimization
# ============================================================================


def collect_training_data(
    scientist: MagnetDiscoveryScientist, num_runs: int = 20
) -> List:
    """Collect training data for DSPy optimization"""
    training_data = []

    # Define various target properties for different applications
    target_scenarios = [
        {  # High-performance motor magnets
            "BHmax_min": 300,  # kJ/m³
            "Hc_min": 1000,  # kA/m
            "Ms_min": 1.5,  # T
            "Tc_min": 450,  # K
        },
        {  # High-temperature applications
            "BHmax_min": 200,
            "Hc_min": 800,
            "Ms_min": 1.2,
            "Tc_min": 600,
        },
        {  # Cost-effective magnets
            "BHmax_min": 150,
            "Hc_min": 600,
            "Ms_min": 1.0,
            "Tc_min": 400,
        },
    ]

    known_materials = [
        "Nd2Fe14B (reference, has rare earths)",
        "Ferrite (Ba,Sr)Fe12O19",
        "Alnico",
        "Fe16N2 (theoretical)",
        "MnBi",
        "MnAl",
    ]

    for run in range(num_runs):
        targets = target_scenarios[run % len(target_scenarios)]

        result = scientist(target_properties=targets, known_materials=known_materials)

        # Create training example
        example = dspy.Example(
            target_properties=json.dumps(targets),
            known_materials=str(known_materials),
            best_hypothesis=(
                result["best_material"]["hypothesis"] if result["best_material"] else ""
            ),
            best_composition=(
                result["best_material"]["material"] if result["best_material"] else ""
            ),
            score=result["best_material"]["score"] if result["best_material"] else 0,
        ).with_inputs("target_properties", "known_materials")

        training_data.append(example)

    return training_data


# ============================================================================
# DSPy Optimization
# ============================================================================


def optimize_scientist(base_scientist: MagnetDiscoveryScientist, training_data: List):
    """Optimize the AI scientist using DSPy teleprompters"""

    # Define metric for optimization
    def discovery_metric(example, prediction, trace=None):
        # Evaluate how good the discovery is
        # In real implementation, this would run actual evaluation
        score = prediction.get("score", 0)
        return score

    # Use BootstrapFewShot to learn from successful discoveries
    from dspy.teleprompt import BootstrapFewShot

    optimizer = BootstrapFewShot(
        metric=discovery_metric, max_bootstrapped_demos=4, max_labeled_demos=4
    )

    # Compile optimized version
    optimized_scientist = optimizer.compile(base_scientist, trainset=training_data)

    return optimized_scientist


# ============================================================================
# Main Execution
# ============================================================================


def main():
    # Configure DSPy with your LLM
    lm = dspy.LM(
        "openai/gpt-4.1-mini",
        api_key=os.getenv("OPENAI_API_KEY"),
        max_tokens=32000,
        cache=False,
        temperature=1,
    )
    dspy.settings.configure(lm=lm)

    # Initialize AI Scientist
    scientist = MagnetDiscoveryScientist(max_iterations=5)

    # Define target properties for a high-performance permanent magnet
    targets = {
        "num_atoms_max": 20,
        "cost_max": 100,  # USD / kg
        "magnetic_density_min": 0.10,
        "curie_temp_min": 500,  # K - well above room temperature
        "e_hull_max": 0.150,  # eV / atom - stable
        "dynamic_stability": True,  # True or False
    }

    known_materials = [
        "Nd2Fe14B",
        "SmCo",
        "FePt",
        "MnBi",
        "Fe16N2",
    ]

    print("Starting AI Scientist for Rare-Earth-Free Permanent Magnet Discovery")
    print("=" * 70)
    # print(f"Target Properties:")
    # print(f"  - Energy Product (BHmax) > {targets['BHmax_min']} kJ/m³")
    # print(f"  - Coercivity (Hc) > {targets['Hc_min']} kA/m")
    # print(f"  - Saturation (Ms) > {targets['Ms_min']} T")
    # print(f"  - Curie Temperature (Tc) > {targets['Tc_min']} K")
    # print("=" * 70)

    # Run discovery
    discovery = scientist(target_properties=targets, known_materials=known_materials)

    # Print results
    if discovery["best_material"]:
        best = discovery["best_material"]
        print("\n" + "=" * 70)
        print("BEST DISCOVERY:")
        print(f"Composition: {best['composition']}")
        print(f"Space Group: {best['space_group_used']}")
        print(f"Hypothesis: {best['hypothesis']}")
        print(f"Score: {best['score']:.3f}")
        print("\nPredicted Properties:")
        for prop, value in best["properties"].items():
            print(f"  - {prop}: {value:.2f}")

    # Optional: Collect training data and optimize
    # print("\n" + "=" * 70)
    # print("Collecting training data for optimization...")
    # training_data = collect_training_data(scientist, num_runs=5)

    # print(f"Optimizing scientist based on {len(training_data)} discovery runs...")
    # optimized_scientist = optimize_scientist(scientist, training_data)

    # print("Optimization complete! The AI Scientist has learned from its discoveries.")

    # Publish run summary to Ouro
    try:
        publisher = Publisher()
        publisher.publish_run_summary(discovery=discovery, targets=targets)
    except Exception as _publish_exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"Publishing to Ouro failed: {_publish_exc}")


if __name__ == "__main__":
    main()
