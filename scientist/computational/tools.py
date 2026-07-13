"""Computational tools facade for material generation and evaluation.

This module provides a unified interface to the computational tools,
delegating to specialized modules for specific functionality.
"""

import os
import time
from typing import Dict, List, Optional, Any

from ..data.models import Material, MaterialProperties
from ..utils.logging import get_logger
from .ouro_client import OuroClient
from .registry import MaterialRegistry
from .structure_generator import StructureGenerator
from .evaluator import MaterialEvaluator

logger = get_logger("tools")


class ComputationalTools:
    """Unified interface to computational materials science tools.

    This class acts as a facade, coordinating between:
    - OuroClient: Ouro platform API interactions
    - StructureGenerator: Crystal structure generation (GGen/Ouro)
    - MaterialEvaluator: Property evaluation via Ouro routes
    - MaterialRegistry: Material tracking and caching
    """

    def __init__(self, config, post_id: Optional[str] = None) -> None:
        """Initialize computational tools.

        Args:
            config: ScientistConfig with API settings
            post_id: Optional Ouro post ID for asset parenting
        """
        # Initialize components
        self.ouro_client = OuroClient(
            team_id=config.ouro_team_id,
            visibility=config.ouro_asset_visibility,
            post_id=post_id,
        )

        self.material_registry = MaterialRegistry()

        self.structure_generator = StructureGenerator(
            ouro_client=self.ouro_client,
            registry=self.material_registry,
        )

        self.evaluator = MaterialEvaluator(
            ouro_client=self.ouro_client,
            registry=self.material_registry,
        )

        logger.info("ComputationalTools initialized")

    # --- Structure Generation ---

    def get_compatible_space_groups(self, formula: str) -> List[int]:
        """Get compatible space groups for a composition.

        Args:
            formula: Chemical formula

        Returns:
            List of compatible space group numbers
        """
        return self.ouro_client.get_compatible_space_groups(formula)

    def generate_structure(
        self,
        composition: str,
        space_group: Optional[str] = None,
        constraints: Optional[Dict] = None,
        use_ggen: bool = True,
    ) -> Material:
        """Generate crystal structure.

        Args:
            composition: Chemical formula
            space_group: Target space group (optional)
            constraints: Generation constraints (optional, currently unused)
            use_ggen: Use GGen (True) or Ouro (False) for generation

        Returns:
            Generated Material
        """
        return self.structure_generator.generate(
            composition=composition,
            space_group=space_group,
            use_ggen=use_ggen,
        )

    def mutate_material(
        self,
        material: Material,
        operations: List[Dict[str, Any]],
    ) -> Material:
        """Apply mutations to a material.

        Args:
            material: Parent material to mutate
            operations: List of mutation operations

        Returns:
            Mutated Material
        """
        return self.structure_generator.mutate(material, operations)

    # --- Property Evaluation ---

    def evaluate_material_properties(self, material: Material) -> MaterialProperties:
        """Evaluate material properties.

        Args:
            material: Material to evaluate

        Returns:
            Computed properties
        """
        return self.evaluator.evaluate_properties(material)

    # --- Registry Access ---

    def get_mutation_history_summary(self) -> List[Dict[str, Any]]:
        """Get summary of all mutations and their effects.

        Returns:
            List of mutation records as dictionaries
        """
        return self.material_registry.get_mutation_history_summary()

    def get_mutation_effectiveness_stats(self) -> Dict[str, Any]:
        """Get statistics about mutation effectiveness.

        Returns:
            Dictionary containing mutation statistics
        """
        return self.material_registry.get_mutation_effectiveness_stats()

    # --- Trajectory Visualization ---

    def get_trajectory_visualization(self) -> Optional[Dict[str, Any]]:
        """Get trajectory visualization by uploading to Ouro.

        Returns:
            Visualization result with trajectory file and frame count
        """
        try:
            trajectory = self.structure_generator.get_trajectory_data()
            if not trajectory or len(trajectory) < 2:
                logger.warning("No trajectory data available (need at least 2 frames)")
                return None

            logger.info(
                f"Generating trajectory visualization from {len(trajectory)} frames"
            )

            # Export trajectory file
            traj_filename = self.structure_generator.export_trajectory()
            if not traj_filename or not os.path.exists(traj_filename):
                logger.warning("Trajectory file not found")
                return None

            # Upload to Ouro
            trajectory_file = self.ouro_client.upload_file(
                file_path=traj_filename,
                name=f"mutation_trajectory_{int(time.time())}.traj",
                description="GGen mutation trajectory for visualization",
            )
            logger.info(f"Uploaded trajectory: {trajectory_file.id}")

            # Generate visualization via Ouro route
            try:
                viz_result = self.ouro_client.execute_route(
                    # mmoderwell/interactive-trajectory-explorer-with-matterviz
                    "ce8224e2-fdf3-4ca4-ad8f-dd04500f3825",
                    trajectory_file.id,
                )
                logger.info("Generated trajectory visualization")
            except Exception as viz_error:
                logger.warning(f"Trajectory visualization failed: {viz_error}")
                viz_result = None

            # Clean up temp file
            try:
                os.unlink(traj_filename)
            except Exception as e:
                logger.warning(f"Failed to clean up {traj_filename}: {e}")

            return {
                "trajectory_file": (
                    trajectory_file.model_dump(mode="json")
                    if hasattr(trajectory_file, "model_dump")
                    else trajectory_file
                ),
                "visualization": viz_result,
                "frame_count": len(trajectory),
            }

        except Exception as e:
            logger.error(f"Failed to generate trajectory visualization: {e}")
            return None
