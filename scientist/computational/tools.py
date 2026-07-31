"""Computational tools facade for material generation and evaluation."""

from typing import Dict, List, Optional

from ..data.models import Material, MaterialProperties, ExplorationSummary
from ..utils.logging import get_logger
from .ouro_client import OuroClient
from .registry import MaterialRegistry
from .structure_generator import StructureGenerator
from .evaluator import MaterialEvaluator
from .explorer import SystemExplorer

logger = get_logger("tools")


class ComputationalTools:
    """Unified interface to computational materials science tools.

    Coordinates:
    - OuroClient: Ouro platform API + hosted GGen routes
    - SystemExplorer: chemical-system exploration via hosted GGen
    - StructureGenerator: single-structure generation fallback
    - MaterialEvaluator: property evaluation via Ouro routes
    - MaterialRegistry: material tracking and caching
    """

    def __init__(self, config, post_id: Optional[str] = None) -> None:
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

        self.system_explorer = SystemExplorer(
            ouro_client=self.ouro_client,
            registry=self.material_registry,
            max_atoms=config.ggen_max_atoms,
            min_atoms=config.ggen_min_atoms,
            num_trials=config.ggen_num_trials,
            e_hull_cutoff=config.ggen_e_hull_cutoff,
            max_candidates=config.max_candidates_to_evaluate,
            max_stoichiometries=config.ggen_max_stoichiometries,
            poll_timeout=config.ggen_poll_timeout,
        )

        self.evaluator = MaterialEvaluator(
            ouro_client=self.ouro_client,
            registry=self.material_registry,
        )

        logger.info("ComputationalTools initialized (hosted GGen)")

    def explore_system(
        self,
        chemical_system: str,
        crystal_systems: Optional[List[str]] = None,
        min_fraction: Optional[Dict[str, float]] = None,
        max_fraction: Optional[Dict[str, float]] = None,
    ) -> tuple[List[Material], ExplorationSummary]:
        """Explore a chemical system with hosted GGen; return near-hull Materials."""
        return self.system_explorer.explore(
            chemical_system=chemical_system,
            crystal_systems=crystal_systems,
            min_fraction=min_fraction,
            max_fraction=max_fraction,
        )

    def generate_structure(
        self,
        composition: str,
        space_group: Optional[str] = None,
        use_ggen: bool = True,
        chemical_system: Optional[str] = None,
    ) -> Material:
        """Generate a single crystal structure (fallback path)."""
        return self.structure_generator.generate(
            composition=composition,
            space_group=space_group,
            use_ggen=use_ggen,
            chemical_system=chemical_system,
        )

    def evaluate_material_properties(self, material: Material) -> MaterialProperties:
        return self.evaluator.evaluate_properties(material)
