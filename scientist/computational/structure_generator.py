"""Crystal structure generation via Ouro-hosted GGen."""

from typing import Dict, List, Optional
from io import StringIO

import requests
from pymatgen.core.structure import Structure
from pymatgen.io.cif import CifParser
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

from ..data.models import Material
from ..utils.logging import get_logger
from .ouro_client import OuroClient
from .registry import MaterialRegistry

logger = get_logger("structure_generator")

SYMPREC = 0.1


class StructureGenerator:
    """Single-structure generation via hosted GGen (fallback path).

    Bulk chemical-system exploration lives in SystemExplorer.
    """

    def __init__(
        self,
        ouro_client: OuroClient,
        registry: MaterialRegistry,
    ) -> None:
        self.ouro = ouro_client
        self.registry = registry

    def generate(
        self,
        composition: str,
        space_group: Optional[str] = None,
        use_ggen: bool = True,
        chemical_system: Optional[str] = None,
        crystal_systems: Optional[List[str]] = None,
        num_trials: int = 10,
    ) -> Material:
        """Generate a single crystal structure via hosted GGen."""
        del use_ggen  # always hosted now
        return self._generate_hosted(
            composition, space_group, chemical_system, crystal_systems, num_trials
        )

    def _generate_hosted(
        self,
        composition: str,
        space_group: Optional[str] = None,
        chemical_system: Optional[str] = None,
        crystal_systems: Optional[List[str]] = None,
        num_trials: int = 10,
    ) -> Material:
        logger.info(
            f"Generating structure with hosted GGen: {composition} (SG: {space_group})"
        )
        requested_sg = self._parse_space_group(space_group)

        result = self.ouro.generate_crystal(
            formula=composition,
            space_group=requested_sg,
            num_trials=num_trials,
            crystal_systems=crystal_systems,
        )

        file_asset = result.get("file") if isinstance(result, dict) else None
        if not file_asset or not file_asset.get("id"):
            raise RuntimeError(f"Hosted GGen returned no CIF file for {composition}")

        file_obj = self.ouro.retrieve_file(file_asset["id"])
        file_data = file_obj.read_data()
        structure_data = requests.get(file_data.url, timeout=120).text
        atoms = CifParser(StringIO(structure_data)).parse_structures(primitive=False)[0]

        resolved_sg = result.get("final_space_group") or self._get_space_group(atoms)
        used_sg = result.get("selected_space_group") or requested_sg

        material = Material(
            composition=composition,
            atoms=atoms,
            num_atoms=len(atoms),
            cif_string=structure_data,
            file=file_asset,
            predicted_properties={},
            requested_space_group=requested_sg,
            used_space_group=used_sg,
            resolved_space_group=resolved_sg,
            generation_method="from_scratch",
            chemical_system=chemical_system,
        )
        self.registry.register(material)
        return material

    def _parse_space_group(self, space_group: Optional[str]) -> Optional[int]:
        if space_group is None:
            return None
        try:
            return int(space_group)
        except (ValueError, TypeError):
            return None

    def _get_space_group(self, structure: Structure) -> Optional[int]:
        try:
            sga = SpacegroupAnalyzer(structure, symprec=SYMPREC)
            return int(sga.get_space_group_number())
        except Exception:
            return None
