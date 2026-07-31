"""DSPy signatures for scientific reasoning."""

import dspy
from typing import List, Dict, Any


class AnalyzeMagnetLandscape(dspy.Signature):
    """Analyze current knowledge of permanent magnets to identify opportunities."""

    constraints = dspy.InputField(desc="what's considered a good opportunity")
    target_properties = dspy.InputField(desc="desired material properties")
    prior_explorations = dspy.InputField(
        desc="summary of chemical systems already explored and their outcomes, if any",
        default="none",
    )
    analysis = dspy.OutputField(desc="analysis of gaps and opportunities")
    promising_directions = dspy.OutputField(
        desc="list of promising research directions"
    )


class ProposeChemicalSystem(dspy.Signature):
    """Propose a chemical system to explore for rare-earth-free permanent magnets.

    Propose element sets (not single formulas). GGen will enumerate stoichiometries,
    generate and relax structures with an MLIP, and return near-hull candidates.
    Prefer systems with magnetic 3d metals (Fe, Co, Mn, Ni) plus anisotropy-promoting
    partners (Bi, Sb, Ge, Si, Al, B, N, C, …). Never include rare-earth elements.
    """

    landscape_analysis = dspy.InputField(
        desc="analysis of magnetic materials landscape"
    )
    previous_explorations = dspy.InputField(
        desc="JSON list of prior exploration summaries (systems, hull hits, scores)"
    )
    guiding_hypothesis = dspy.InputField(
        desc="refined overall hypothesis guiding this run, or 'none' on first iteration"
    )
    target_properties = dspy.InputField(desc="desired magnetic properties")
    explored_systems = dspy.InputField(
        desc="comma-separated list of systems already explored this run"
    )

    chemical_system = dspy.OutputField(
        desc="dash-separated elements, e.g. Fe-Co-Bi or Mn-Al-C (2-4 elements)"
    )
    crystal_systems = dspy.OutputField(
        desc="comma-separated preferred crystal systems "
        "(tetragonal, hexagonal, cubic, orthorhombic, trigonal) or 'all'"
    )
    min_fraction = dspy.OutputField(
        desc='JSON object of minimum element fractions, e.g. {"Fe": 0.3}, or {}'
    )
    max_fraction = dspy.OutputField(
        desc='JSON object of maximum element fractions, e.g. {"Bi": 0.2}, or {}'
    )
    hypothesis = dspy.OutputField(
        desc="scientific hypothesis for why this system should yield good magnets"
    )
    rationale = dspy.OutputField(desc="reasoning connecting hypothesis to constraints")


class InterpretExplorationResults(dspy.Signature):
    """Interpret GGen exploration and magnetic evaluation results."""

    chemical_system = dspy.InputField(desc="chemical system that was explored")
    hypothesis = dspy.InputField(desc="hypothesis that motivated this exploration")
    exploration_summary = dspy.InputField(
        desc="JSON summary: hull phases, crystal systems, candidate counts"
    )
    evaluated_candidates = dspy.InputField(
        desc="JSON list of Ouro-evaluated candidates with properties and scores"
    )
    target_properties = dspy.InputField(desc="desired material properties")

    analysis = dspy.OutputField(desc="how well the system performed vs targets")
    insights = dspy.OutputField(desc="what we learned for the next exploration")
    next_directions = dspy.OutputField(
        desc="suggested follow-up chemical systems or constraints"
    )


class RefineHypothesis(dspy.Signature):
    """Refine the discovery hypothesis based on exploration outcomes."""

    original_hypothesis = dspy.InputField(desc="original hypothesis")
    exploration_history = dspy.InputField(
        desc="JSON list of exploration summaries across iterations"
    )
    best_results = dspy.InputField(
        desc="JSON list of top-scoring evaluated materials so far"
    )
    iteration = dspy.InputField(desc="current iteration number")

    refined_hypothesis = dspy.OutputField(desc="improved hypothesis")
    modifications = dspy.OutputField(desc="specific changes made")
    confidence_score = dspy.OutputField(desc="confidence in new hypothesis (0-1)")
