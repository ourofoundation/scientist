"""DSPy signatures for scientific reasoning."""

import dspy
from typing import List, Optional, Dict, Any
from ..data.models import Material


class AnalyzeMagnetLandscape(dspy.Signature):
    """Analyze current knowledge of permanent magnets to identify opportunities."""

    # Input fields
    constraints = dspy.InputField(desc="what's considered a good opportunity")
    target_properties = dspy.InputField(desc="desired material properties")
    # Output fields
    analysis = dspy.OutputField(desc="analysis of gaps and opportunities")
    promising_directions = dspy.OutputField(
        desc="list of promising research directions"
    )


class GenerateMagnetHypothesis(dspy.Signature):
    """Generate hypothesis for new permanent magnet material."""

    # Input fields
    previous_results = dspy.InputField(desc="results from previous iterations")
    landscape_analysis = dspy.InputField(
        desc="analysis of magnetic materials landscape"
    )
    design_strategy = dspy.InputField(desc="selected design strategy")
    # Output fields
    hypothesis = dspy.OutputField(
        desc="specific hypothesis about material composition/structure"
    )
    rationale = dspy.OutputField(desc="scientific reasoning behind hypothesis")
    expected_properties = dspy.OutputField(desc="predicted magnetic properties")


class DesignMaterialCandidate(dspy.Signature):
    """Design specific material candidate based on hypothesis."""

    # Input fields
    hypothesis = dspy.InputField(desc="scientific hypothesis")
    constraints = dspy.InputField(
        desc="constraints on the material composition and structure"
    )
    compatible_space_groups: List[int] = dspy.InputField(
        desc="list of compatible space groups for the composition (if available)",
        default=[],
    )
    # Output fields
    composition = dspy.OutputField(
        desc="chemical composition using whole value notation"
    )
    space_group = dspy.OutputField(
        desc="space group number 1-230 (must be from compatible list if provided)"
    )


class InterpretSimulationResults(dspy.Signature):
    """Interpret computational results and extract insights."""

    # Input fields
    material: Dict[str, Any] = dspy.InputField(desc="material details")
    target_properties: Dict[str, Any] = dspy.InputField(
        desc="desired material properties"
    )
    mutation_applied = dspy.InputField(
        desc="mutation that was applied (if any)", default="none"
    )
    parent_properties = dspy.InputField(
        desc="parent material properties for comparison (if mutation)", default="none"
    )
    # Output fields
    analysis = dspy.OutputField(desc="how well material meets targets")
    insights = dspy.OutputField(desc="what we learned")


class RefineHypothesis(dspy.Signature):
    """Refine hypothesis based on computational results."""

    original_hypothesis = dspy.InputField(desc="original hypothesis")
    results = dspy.InputField(desc="computational evaluation results")
    insights = dspy.InputField(desc="key insights from analysis")
    iteration = dspy.InputField(desc="current iteration number")
    mutation_history = dspy.InputField(
        desc="history of successful and failed mutations with property changes"
    )

    refined_hypothesis = dspy.OutputField(desc="improved hypothesis")
    modifications = dspy.OutputField(desc="specific changes made")
    confidence_score = dspy.OutputField(desc="confidence in new hypothesis (0-1)")


"""
    - change_space_group(target_space_group, symprec): Change space group
        - change_space_group(target_space_group=225, symprec=0.1)
"""


class GenerateMutationOperations(dspy.Signature):
    """Generate mutation operations based on current material and objectives.

    Available operations and their parameters:
    - scale_lattice(scale_factor, isotropic=True): Scale lattice parameters
        - scale_lattice(scale_factor=1.1, isotropic=True)
        - scale_lattice(scale_factor=[1.1, 0.9, 1.0], isotropic=False)
    - shear_lattice(angle_deltas): Modify lattice angles
        - shear_lattice(angle_deltas=[10, 10, 10])
    - substitute(element_from, element_to, fraction=1.0): Element substitution
        - substitute(element_from="Fe", element_to="Ni", fraction=1.0)
    - add_site(element, coordinates): Add atomic sites, coordinates are in fractional coordinates
        - add_site(element="Ni", coordinates=[0.1, 0.2, 0.3])
    - remove_site(site_indices, element): Remove atomic sites
        - remove_site(site_indices=[0, 1], element="Fe")
    - move_site(site_index, displacement=None, new_coordinates=None): Move atomic sites
        - move_site(site_index=0, displacement=[0.1, 0.2, 0.3])
    - jitter_sites(sigma, element=None): Add random displacements
        - jitter_sites(sigma=0.01, element="Fe")
    - symmetry_break(displacement_scale, angle_perturbation): Break symmetry by displacing atoms and perturbing lattice angles
        - symmetry_break(displacement_scale=0.01, angle_perturbation=0.1)


    Output format:
    [
        {"op": "function_name", "param1": value1, "param2": value2, ...},
        {"op": "function_name", "param1": value1, "param2": value2, ...},
        ...
    ]

    Choose up to 5 operations to apply to the material.
    Geometry optimization will be applied to the material after applying operations.
    Attempt to move the material towards an adjacent energy minimum.
    """

    # Input fields
    iteration: int = dspy.InputField(desc="current iteration number (0-based)")
    material: Material = dspy.InputField(desc="chosen material to mutate")
    target_properties = dspy.InputField(desc="desired material properties")
    mutation_history = dspy.InputField(
        desc="history of mutations and their effects on properties"
    )
    # Output fields
    operations: List[Dict[str, Any]] = dspy.OutputField(desc="Operations array")
    rationale: str = dspy.OutputField(
        desc="scientific reasoning for this strategy choice"
    )


class DecideGenerationMode(dspy.Signature):
    """Decide whether to generate a new material or mutate a previous one."""

    # Input fields
    iteration: int = dspy.InputField(desc="current iteration number (0-based)")
    current_material = dspy.InputField(
        desc="current best material composition and properties"
    )
    target_properties = dspy.InputField(desc="desired magnetic properties")
    mutation_history = dspy.InputField(
        desc="history of mutations and their effects on properties"
    )
    available_materials = dspy.InputField(
        desc="list of available materials from generation history with their IDs and properties"
    )

    # Output fields
    decision = dspy.OutputField(desc="'new' or 'mutate'")
    target_material_id = dspy.OutputField(
        desc="material id to mutate from if decision is mutate"
    )
    rationale = dspy.OutputField(desc="scientific reasoning for this decision")
