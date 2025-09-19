"""Material scoring and evaluation logic."""

from typing import Dict, Any
from ..data.models import Material, MaterialProperties


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
        """Calculate overall material score with correct directionality and clamping.

        Args:
            material: The material being scored
            properties: Computed properties of the material
            targets: Target property values

        Returns:
            Score between 0.0 and 1.0, where 1.0 is perfect
        """
        score = 0.0

        # Lower is better properties
        if "num_atoms" in self.weights:
            score += self._score_lower_is_better(
                material.num_atoms,
                targets.get("num_atoms_max"),
                self.weights["num_atoms"],
            )

        if "e_hull" in self.weights:
            score += self._score_lower_is_better(
                properties.e_hull, targets.get("e_hull_max"), self.weights["e_hull"]
            )

        if "cost" in self.weights:
            score += self._score_lower_is_better(
                properties.cost, targets.get("cost_max"), self.weights["cost"]
            )

        # Higher is better properties
        if "magnetic_density" in self.weights:
            score += self._score_higher_is_better(
                properties.magnetic_density,
                targets.get("magnetic_density_min"),
                self.weights["magnetic_density"],
            )

        if "curie_temperature" in self.weights:
            score += self._score_higher_is_better(
                properties.curie_temperature,
                targets.get("curie_temperature_min"),
                self.weights["curie_temperature"],
            )

        # Boolean properties
        if "dynamic_stability" in self.weights:
            target_stability = targets.get("dynamic_stability", True)
            if target_stability:
                score += self.weights["dynamic_stability"] * (
                    1.0 if properties.dynamic_stability else 0.0
                )

        # Space group penalty (penalize low space group numbers like P1)
        # Materials that relax to low-symmetry space groups (especially P1)
        # are often undesirable even if other properties look good
        if "space_group_penalty" in self.weights:
            min_space_group = targets.get("min_space_group", 8)
            if material.resolved_space_group is not None:
                if material.resolved_space_group < min_space_group:
                    # Apply proportional penalty for low space group numbers
                    # e.g., P1 (sg=1) with min=8 gets penalty_factor=0.125
                    penalty_factor = material.resolved_space_group / min_space_group
                    score += self.weights["space_group_penalty"] * penalty_factor
                else:
                    # No penalty for acceptable space groups
                    score += self.weights["space_group_penalty"]

        return max(0.0, min(1.0, score))

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
