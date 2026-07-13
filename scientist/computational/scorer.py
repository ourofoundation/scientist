"""Material scoring and evaluation logic."""

from typing import Dict, Any

from ..data.models import Material, MaterialProperties
from ..utils.logging import get_logger

logger = get_logger("scorer")


class MaterialScorer:
    """Handles scoring of materials against target properties."""

    def __init__(self, weights: Dict[str, float]):
        """Initialize scorer with property weights.

        Args:
            weights: Dictionary mapping property names to their weights.
                    Weights should sum to 1.0.
        """
        self.weights = weights
        self._validate_weights()

    def _validate_weights(self) -> None:
        """Validate that weights sum to 1.0."""
        total_weight = sum(self.weights.values())
        if abs(total_weight - 1.0) > 1e-6:
            raise ValueError(f"Weights must sum to 1.0, got {total_weight}")

    def calculate_score(
        self,
        material: Material,
        properties: MaterialProperties,
        targets: Dict[str, Any],
    ) -> float:
        """Calculate overall material score.

        Handles partial property evaluation gracefully - properties that are None
        are skipped and the score is calculated from available properties with
        adjusted weights.

        Args:
            material: The material being scored
            properties: Computed properties (may have None values)
            targets: Target property values

        Returns:
            Score between 0.0 and 1.0, where 1.0 is perfect
        """
        score = 0.0
        total_weight_used = 0.0
        skipped = []

        # Lower is better
        for prop, target_key in [
            ("num_atoms", "num_atoms_max"),
            ("e_hull", "e_hull_max"),
            ("cost", "cost_max"),
        ]:
            if prop not in self.weights:
                continue

            value = (
                getattr(material, prop, None)
                if prop == "num_atoms"
                else getattr(properties, prop, None)
            )

            if value is not None:
                score += self._score_lower_is_better(
                    value, targets.get(target_key), self.weights[prop]
                )
                total_weight_used += self.weights[prop]
            else:
                skipped.append(prop)

        # Higher is better
        for prop, target_key in [
            ("magnetic_density", "magnetic_density_min"),
            ("magnetic_anisotropy_energy", "magnetic_anisotropy_energy_min"),
            ("curie_temperature", "curie_temperature_min"),
        ]:
            if prop not in self.weights:
                continue

            value = getattr(properties, prop, None)

            if value is not None:
                score += self._score_higher_is_better(
                    value, targets.get(target_key), self.weights[prop]
                )
                total_weight_used += self.weights[prop]
            else:
                skipped.append(prop)

        # Boolean: dynamic stability
        if "dynamic_stability" in self.weights:
            if properties.dynamic_stability is not None:
                target_stability = targets.get("dynamic_stability", True)
                if target_stability:
                    score += self.weights["dynamic_stability"] * (
                        1.0 if properties.dynamic_stability else 0.0
                    )
                total_weight_used += self.weights["dynamic_stability"]
            else:
                skipped.append("dynamic_stability")

        # Space group penalty
        if "space_group_penalty" in self.weights:
            min_sg = targets.get("min_space_group", 8)
            if material.resolved_space_group is not None:
                if material.resolved_space_group < min_sg:
                    penalty_factor = material.resolved_space_group / min_sg
                    score += self.weights["space_group_penalty"] * penalty_factor
                else:
                    score += self.weights["space_group_penalty"]
                total_weight_used += self.weights["space_group_penalty"]
            else:
                skipped.append("space_group_penalty")

        # Normalize
        if total_weight_used > 0:
            normalized_score = score / total_weight_used
        else:
            normalized_score = 0.0

        if skipped:
            logger.debug(
                f"Skipped {len(skipped)} properties (None values), "
                f"scored from {total_weight_used:.1%} of weight"
            )

        return max(0.0, min(1.0, normalized_score))

    def _score_lower_is_better(
        self, actual: float, target_max: Any, weight: float
    ) -> float:
        """Score a property where lower values are better."""
        if not isinstance(target_max, (int, float)) or target_max <= 0:
            return 0.0
        return weight * max(0.0, 1.0 - actual / target_max)

    def _score_higher_is_better(
        self, actual: float, target_min: Any, weight: float
    ) -> float:
        """Score a property where higher values are better."""
        if not isinstance(target_min, (int, float)) or target_min <= 0:
            return 0.0
        return weight * min(1.0, actual / target_min)
