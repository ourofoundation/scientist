"""Crystal structure generation using GGen and Ouro."""

import os
from datetime import datetime
from typing import Dict, List, Any, Optional

import numpy as np
from pymatgen.core.structure import Structure
from pymatgen.io.cif import CifParser, CifWriter
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
from io import StringIO
import requests

from ggen import GGen

from ..data.models import Material, MutationRecord
from ..utils.logging import get_logger
from .ouro_client import OuroClient
from .registry import MaterialRegistry

logger = get_logger("structure_generator")

SYMPREC = 0.1


class StructureGenerator:
    """Handles crystal structure generation via GGen and Ouro."""

    def __init__(
        self,
        ouro_client: OuroClient,
        registry: MaterialRegistry,
    ) -> None:
        """Initialize structure generator.

        Args:
            ouro_client: Ouro API client
            registry: Material registry for tracking
        """
        self.ouro = ouro_client
        self.registry = registry

        # Initialize GGen
        self.ggen = GGen(enable_trajectory=True)
        logger.info("GGen initialized with ORB calculator")

    def generate(
        self,
        composition: str,
        space_group: Optional[str] = None,
        use_ggen: bool = True,
    ) -> Material:
        """Generate crystal structure.

        Args:
            composition: Chemical formula
            space_group: Target space group (optional)
            use_ggen: Use GGen (True) or Ouro (False) for generation

        Returns:
            Generated Material
        """
        if use_ggen:
            return self._generate_ggen(composition, space_group)
        return self._generate_ouro(composition, space_group)

    def _generate_ggen(
        self,
        composition: str,
        space_group: Optional[str] = None,
    ) -> Material:
        """Generate structure using GGen.

        Args:
            composition: Chemical formula
            space_group: Target space group

        Returns:
            Generated Material
        """
        logger.info(
            f"Generating structure with GGen: {composition} (SG: {space_group})"
        )

        requested_sg = self._parse_space_group(space_group)

        try:
            result = self.ggen.generate_crystal(
                formula=composition,
                space_group=requested_sg,
                num_trials=40,
                optimize_geometry=True,
            )

            structure = self.ggen.get_structure()
            cif_content = result.get("cif_content", "")

        except Exception as e:
            logger.warning(f"GGen generation failed: {e}, falling back to Ouro")
            return self._generate_ouro(composition, space_group)

        resolved_sg = self._get_space_group(structure)
        mock_file = self._create_mock_file(composition, requested_sg, len(structure))

        material = Material(
            composition=composition,
            atoms=structure,
            num_atoms=len(structure),
            cif_string=cif_content,
            file=mock_file,
            predicted_properties={},
            requested_space_group=requested_sg,
            used_space_group=requested_sg,
            resolved_space_group=resolved_sg,
            generation_method="from_scratch",
        )

        self.registry.register(material)
        return material

    def _generate_ouro(
        self,
        composition: str,
        space_group: Optional[str] = None,
    ) -> Material:
        """Generate structure using Ouro crystal generator.

        Args:
            composition: Chemical formula
            space_group: Target space group

        Returns:
            Generated Material
        """
        logger.info(
            f"Generating structure with Ouro: {composition} (SG: {space_group})"
        )

        requested_sg = self._parse_space_group(space_group)
        used_sg = self._resolve_space_group(composition, requested_sg)

        response = self.ouro.generate_crystal(
            formula=composition,
            space_group=used_sg,
        )

        file = self.ouro.retrieve_file(response["file"]["id"])
        file_data = file.read_data()
        structure_data = requests.get(file_data.url).text
        atoms = CifParser(StringIO(structure_data)).parse_structures(primitive=False)[0]

        resolved_sg = self._get_space_group(atoms)

        material = Material(
            composition=composition,
            atoms=atoms,
            num_atoms=len(atoms),
            cif_string=structure_data,
            file=file.model_dump(mode="json"),
            predicted_properties={},
            requested_space_group=requested_sg,
            used_space_group=used_sg,
            resolved_space_group=resolved_sg,
            generation_method="from_scratch",
        )

        self.registry.register(material)
        return material

    def mutate(
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
        logger.info(
            f"Mutating {material.composition} with {len(operations)} operations"
        )
        logger.debug(f"Operations: {[op.get('op', 'unknown') for op in operations]}")

        self.ggen.set_structure(material.atoms, add_to_trajectory=True)

        mutation_record = MutationRecord(
            mutation_type="multiple_mutations",
            parameters={"operations": operations},
            timestamp=datetime.now(),
            parent_composition=material.composition,
            child_composition="",
        )

        try:
            self.ggen.mutate_crystal(
                operations=operations, repair=True, min_distance=0.8
            )
            mutated_structure = self.ggen.get_structure()

            # Optimize geometry
            try:
                logger.debug("Optimizing geometry...")
                self.ggen.optimize_geometry()
                mutated_structure = self.ggen.get_structure()
                logger.debug("Geometry optimization completed")
            except Exception as e:
                logger.warning(f"Optimization failed: {e}, using unoptimized structure")

            # Calculate similarity
            similarity_score = self._calculate_similarity(material.atoms)
            logger.debug(f"Similarity score: {similarity_score:.3f}")

            new_composition = mutated_structure.composition.reduced_formula
            resolved_sg = self._get_space_group(mutated_structure)

            cif_writer = CifWriter(mutated_structure)
            cif_content = str(cif_writer)

            mock_file = self._create_mutation_mock_file(
                new_composition, material, operations
            )

            mutation_record.child_composition = new_composition
            mutation_record.success = True
            mutation_record.property_changes = {
                "similarity_score": similarity_score,
                "mutation_effective": similarity_score <= 0.95,
                "num_operations": len(operations),
                "operation_types": [op.get("op", "unknown") for op in operations],
            }

            mutated_material = Material(
                composition=new_composition,
                atoms=mutated_structure,
                num_atoms=len(mutated_structure),
                cif_string=cif_content,
                file=mock_file,
                predicted_properties={},
                resolved_space_group=resolved_sg,
                generation_method="multiple_mutations",
                parent_material_id=material.material_id,
                mutation_history=material.mutation_history + [mutation_record],
            )

            self.registry.register(mutated_material)
            return mutated_material

        except Exception as e:
            logger.error(f"Mutation failed: {e}")
            mutation_record.success = False
            mutation_record.error_message = str(e)

            failed_material = Material(
                composition=material.composition,
                atoms=material.atoms,
                num_atoms=material.num_atoms,
                cif_string=material.cif_string,
                file=material.file,
                predicted_properties=material.predicted_properties,
                resolved_space_group=material.resolved_space_group,
                generation_method="mutation_failed",
                parent_material_id=material.material_id,
                mutation_history=material.mutation_history + [mutation_record],
            )

            return failed_material

    def get_trajectory_data(self) -> Optional[List]:
        """Get trajectory data from GGen.

        Returns:
            Trajectory frames if available
        """
        if not hasattr(self.ggen, "get_trajectory"):
            return None
        return self.ggen.get_trajectory()

    def export_trajectory(self) -> Optional[str]:
        """Export trajectory to file.

        Returns:
            Trajectory filename if successful
        """
        return self.ggen.export_trajectory()

    def _parse_space_group(self, space_group: Optional[str]) -> Optional[int]:
        """Parse space group string to integer."""
        if space_group is None:
            return None
        try:
            return int(space_group)
        except (ValueError, TypeError):
            return None

    def _resolve_space_group(
        self,
        composition: str,
        requested_sg: Optional[int],
    ) -> Optional[int]:
        """Resolve requested space group to compatible one.

        Args:
            composition: Chemical formula
            requested_sg: Requested space group number

        Returns:
            Compatible space group number
        """
        if requested_sg is None:
            logger.debug(f"No space group specified for {composition}")
            return None

        compatible = self.ouro.get_compatible_space_groups(composition)

        if requested_sg not in compatible:
            logger.warning(
                f"SG {requested_sg} not compatible with {composition}, "
                f"selecting from {len(compatible)} compatible groups"
            )
            used_sg = int(np.random.choice(compatible))
            logger.info(f"Selected compatible SG: {used_sg}")
            return used_sg

        logger.debug(f"Using requested SG: {requested_sg}")
        return requested_sg

    def _get_space_group(self, structure: Structure) -> Optional[int]:
        """Get space group number from structure."""
        try:
            sga = SpacegroupAnalyzer(structure, symprec=SYMPREC)
            return int(sga.get_space_group_number())
        except Exception:
            return None

    def _calculate_similarity(self, parent_structure: Structure) -> float:
        """Calculate similarity between parent and current GGen structure."""
        result = self.ggen.calculate_similarity(parent_structure)
        return float(result["score"])

    def _create_mock_file(
        self,
        composition: str,
        space_group: Optional[int],
        num_atoms: int,
    ) -> Dict[str, Any]:
        """Create mock file object for GGen-generated structures."""
        timestamp = datetime.now().isoformat()
        return {
            "id": f"ggen_{composition}_{timestamp}",
            "name": f"{composition}_SG{space_group or 'auto'}_ggen_{timestamp}",
            "description": f"GGen-generated crystal structure for {composition}",
            "metadata": {
                "composition": composition,
                "generation_method": "ggen",
                "space_group": space_group,
                "num_atoms": num_atoms,
                "timestamp": timestamp,
            },
        }

    def _create_mutation_mock_file(
        self,
        new_composition: str,
        parent: Material,
        operations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Create mock file for mutated structure."""
        timestamp = datetime.now().isoformat()
        return {
            "id": f"ggen_multi_mut_{new_composition}_{timestamp}",
            "name": f"{new_composition}_multi_mutated_{timestamp}",
            "description": f"GGen multi-mutated: {new_composition} from {parent.composition}",
            "metadata": {
                "composition": new_composition,
                "generation_method": "multiple_mutations",
                "parent_id": parent.material_id,
                "parent_composition": parent.composition,
                "mutation_operations": operations,
                "timestamp": timestamp,
            },
        }
