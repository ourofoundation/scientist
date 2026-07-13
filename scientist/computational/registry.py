"""Material registry for tracking materials and caching."""

from typing import Dict, List, Any, Optional
from datetime import datetime

from ..data.models import Material, MaterialProperties, MutationRecord
from ..utils.logging import get_logger

logger = get_logger("registry")


class MaterialRegistry:
    """Registry for tracking materials and their mutation lineage."""

    def __init__(self) -> None:
        """Initialize empty registry."""
        self._materials: Dict[str, Material] = {}
        self._properties_cache: Dict[str, MaterialProperties] = {}

    def register(self, material: Material) -> None:
        """Register a material in the registry.

        Args:
            material: Material to register
        """
        self._materials[material.material_id] = material
        logger.debug(
            f"Registered material: {material.material_id} ({material.composition})"
        )

    def get(self, material_id: str) -> Optional[Material]:
        """Get a material by ID.

        Args:
            material_id: Material identifier

        Returns:
            Material if found, None otherwise
        """
        return self._materials.get(material_id)

    def __contains__(self, material_id: str) -> bool:
        """Check if material exists in registry."""
        return material_id in self._materials

    def __getitem__(self, material_id: str) -> Material:
        """Get material by ID (dict-style access)."""
        return self._materials[material_id]

    def values(self):
        """Iterate over all registered materials."""
        return self._materials.values()

    def cache_properties(self, file_id: str, properties: MaterialProperties) -> None:
        """Cache computed properties for a file.

        Args:
            file_id: Ouro file ID
            properties: Computed properties
        """
        self._properties_cache[file_id] = properties
        logger.debug(f"Cached properties for file: {file_id}")

    def get_cached_properties(self, file_id: str) -> Optional[MaterialProperties]:
        """Get cached properties for a file.

        Args:
            file_id: Ouro file ID

        Returns:
            Cached properties if available
        """
        return self._properties_cache.get(file_id)

    def get_mutation_history_summary(self) -> List[Dict[str, Any]]:
        """Get summary of all mutations and their effects.

        Returns:
            List of mutation records as dictionaries
        """
        history = []
        for material in self._materials.values():
            for mutation in material.mutation_history:
                history.append(
                    {
                        "mutation_type": mutation.mutation_type,
                        "parameters": mutation.parameters,
                        "parent_composition": mutation.parent_composition,
                        "child_composition": mutation.child_composition,
                        "success": mutation.success,
                        "property_changes": mutation.property_changes,
                        "timestamp": mutation.timestamp.isoformat(),
                    }
                )
        return history

    def get_mutation_effectiveness_stats(self) -> Dict[str, Any]:
        """Get statistics about mutation effectiveness.

        Returns:
            Dictionary containing mutation statistics
        """
        stats = {
            "total_mutations": 0,
            "effective_mutations": 0,
            "ineffective_mutations": 0,
            "by_mutation_type": {},
            "average_similarity_scores": {},
            "num_operations": [],
            "operation_types": {},
        }

        for material in self._materials.values():
            for mutation in material.mutation_history:
                if not mutation.success:
                    continue

                stats["total_mutations"] += 1
                mutation_type = mutation.mutation_type

                if mutation_type not in stats["by_mutation_type"]:
                    stats["by_mutation_type"][mutation_type] = {
                        "total": 0,
                        "effective": 0,
                        "ineffective": 0,
                        "similarity_scores": [],
                    }

                stats["by_mutation_type"][mutation_type]["total"] += 1

                if mutation.property_changes:
                    similarity_score = mutation.property_changes.get(
                        "similarity_score", 1.0
                    )
                    is_effective = mutation.property_changes.get(
                        "mutation_effective", False
                    )

                    if isinstance(similarity_score, (int, float)):
                        stats["by_mutation_type"][mutation_type][
                            "similarity_scores"
                        ].append(similarity_score)

                    if is_effective:
                        stats["effective_mutations"] += 1
                        stats["by_mutation_type"][mutation_type]["effective"] += 1
                    else:
                        stats["ineffective_mutations"] += 1
                        stats["by_mutation_type"][mutation_type]["ineffective"] += 1

                    # Track multiple mutation stats
                    if mutation_type == "multiple_mutations":
                        num_ops = mutation.property_changes.get("num_operations", 0)
                        op_types = mutation.property_changes.get("operation_types", [])
                        stats["num_operations"].append(num_ops)
                        for op_type in op_types:
                            stats["operation_types"][op_type] = (
                                stats["operation_types"].get(op_type, 0) + 1
                            )

        # Calculate average similarity scores
        for mutation_type, data in stats["by_mutation_type"].items():
            if data["similarity_scores"]:
                stats["average_similarity_scores"][mutation_type] = sum(
                    data["similarity_scores"]
                ) / len(data["similarity_scores"])
            else:
                stats["average_similarity_scores"][mutation_type] = "no_data"

        return stats
