"""Computational tools interface for material generation and evaluation."""

import dspy
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from typing import List, Dict, Optional, Tuple, Any
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
from ..data.models import Material, MaterialProperties, MutationRecord
from ggen import GGen
from datetime import datetime
from functools import lru_cache

dotenv.load_dotenv(override=True)

SYMPREC = 0.1


class ComputationalTools:
    """Interface to computational materials science tools"""

    def __init__(self, config, post_id: Optional[str] = None) -> None:
        self.ouro = Ouro(api_key=os.getenv("OURO_API_KEY"))
        self.team_id = config.ouro_team_id
        self.visibility = config.ouro_asset_visibility
        self.post_id: Optional[str] = post_id

        # Initialize GGen with error handling for missing ORB models
        self.ggen = GGen(enable_trajectory=True)
        print("✓ GGen initialized successfully with ORB calculator")

        self.material_registry = {}  # Track materials by ID for mutation lineage
        self._similarity_cache: Dict[tuple, float] = {}
        self._properties_cache: Dict[str, MaterialProperties] = {}

    # --- Ouro helpers ---
    @lru_cache(maxsize=512)
    def get_compatible_space_groups(self, formula: str) -> List[int]:
        """Return compatible space groups for a composition. Cached to avoid repeated calls."""
        compatible = self.ouro.routes.use(
            "mmoderwell/get-crystal-gen-compatible-space-groups",
            query={
                "formula": formula,
            },
        )
        return [int(g["number"]) for g in compatible.get("compatible_space_groups", [])]

    def generate_structure(
        self,
        composition: str,
        space_group: str = None,
        constraints: Dict = None,
        use_ggen: bool = True,
    ) -> Material:
        """Generate crystal structure using Ouro or GGen"""

        # Use GGen by default, fall back to Ouro only if explicitly requested
        if use_ggen:
            return self.generate_structure_ggen(composition, space_group, constraints)

        print(f"Generating structure for {composition} with space group {space_group}")

        # Track requested space group from the agent and determine a compatible one
        requested_space_group: Optional[int] = None
        used_space_group: Optional[int] = None
        if space_group is not None:
            try:
                requested_space_group = int(space_group)
            except Exception:  # noqa: BLE001
                requested_space_group = None

        # If an SG was requested, validate and possibly rewrite to a compatible one
        if requested_space_group is not None:
            compatible_space_groups = self.get_compatible_space_groups(composition)
            if requested_space_group not in compatible_space_groups:
                print(
                    f"Space group {requested_space_group} is not compatible with {composition}"
                )
                print(
                    f"Compatible space groups: {compatible_space_groups[:10]}..."
                )  # Show first 10
                used_space_group = int(np.random.choice(compatible_space_groups))
                print(f"Selected compatible space group: {used_space_group}")
            else:
                used_space_group = requested_space_group
                print(f"Using requested space group: {used_space_group}")
        else:
            # No space group requested, let the system choose automatically
            print(
                f"No space group specified for {composition}, letting system choose automatically"
            )
            used_space_group = None

        response = self.ouro.routes.use(
            "mmoderwell/post-crystal-gen-generate",
            body={
                "formula": composition,
                "space_group": used_space_group,
                "num_crystals": 50,
                "optimize_geometry": True,
            },
            output={"team_id": self.team_id},
        )
        file = self.ouro.files.retrieve(response["file"]["id"])
        file_data = file.read_data()
        # CIF string
        structure_data = requests.get(file_data.url).text
        atoms = CifParser(StringIO(structure_data)).parse_structures(primitive=False)[0]

        # Determine the resolved space group from the generated structure
        try:
            sga = SpacegroupAnalyzer(atoms, symprec=SYMPREC)
            resolved_space_group = int(sga.get_space_group_number())
        except Exception:  # noqa: BLE001
            resolved_space_group = None

        # Create material with Ouro generation
        material = Material(
            composition=composition,
            atoms=atoms,
            num_atoms=len(atoms),
            cif_string=structure_data,
            file=file.model_dump(mode="json"),
            predicted_properties={},
            requested_space_group=requested_space_group,
            used_space_group=used_space_group,
            resolved_space_group=resolved_space_group,
            generation_method="from_scratch",
        )

        self.material_registry[material.material_id] = material
        return material

    def evaluate_material_properties(self, material: Material) -> MaterialProperties:
        """Evaluate magnetic properties using computational tools"""

        print(f"Evaluating material properties for {material.composition}")

        # Short-circuit on cache or prior evaluation
        try:
            if (
                isinstance(material.predicted_properties, dict)
                and material.predicted_properties
            ):
                return MaterialProperties(**material.predicted_properties)
        except Exception:
            pass

        # Handle GGen files by uploading the CIF content to Ouro first
        if isinstance(material.file, dict) and material.file.get("id", "").startswith(
            "ggen_"
        ):
            print("Converting GGen structure to Ouro file for property evaluation")
            try:
                # Create a real Ouro file from the CIF content
                cif_filename = f"{material.composition.replace(' ', '_')}.cif"
                # Save CIF content to temporary file and upload
                import tempfile

                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".cif", delete=False
                ) as temp_file:
                    temp_file.write(material.cif_string)
                    temp_file_path = temp_file.name

                try:
                    # Create rich file name and description using available metadata
                    file_name = self._generate_rich_filename(material)
                    file_description = self._generate_rich_description(material)

                    uploaded_file = self.ouro.files.create(
                        file_path=temp_file_path,
                        name=file_name,
                        description=file_description,
                        visibility=self.visibility,
                        team_id=self.team_id,
                        parent_id=self.post_id,
                    )
                finally:
                    os.unlink(temp_file_path)

                # Update the material with the real Ouro file
                material.file = uploaded_file.model_dump(mode="json")
                print(f"✓ Uploaded GGen structure as Ouro file: {uploaded_file.id}")

            except Exception as e:
                print(f"Failed to upload GGen structure to Ouro: {e}")
                raise e
        # Properties cache by file id (works for Ouro files and GGen mock ids)
        try:
            file_id = material.file["id"] if isinstance(material.file, dict) else None
        except Exception:
            file_id = None

        if file_id and file_id in self._properties_cache:
            cached = self._properties_cache[file_id]
            # Keep material.predicted_properties in sync
            material.predicted_properties = cached.__dict__
            print(f"Using cached properties for {material.composition} ({file_id})")
            return cached

        # Calls to Ouro routes
        calls = [
            {
                "name": "hermes/post-structure-cost",
                "body": {
                    "file": material.file,
                },
            },
            {
                "name": "hermes/post-magnetism-curie-temperature",
                "body": {
                    "file": material.file,
                },
            },
            {
                "name": "hermes/post-magnetism-magnetic-saturation",
                "body": {
                    "file": material.file,
                },
            },
            {
                "name": "mmoderwell/post-materials-thermo-ehull",
                "body": {
                    "file": material.file,
                },
            },
            {
                "name": "mmoderwell/post-materials-phonons-dispersion",
                "body": {
                    "file": material.file,
                },
            },
        ]

        # Execute all route calls concurrently

        start_time = time.time()
        results = {}
        errors = {}
        max_workers = min(8, len(calls)) if len(calls) > 0 else 1

        def invoke(call_config: Dict) -> Tuple[str, Dict]:
            route_name = call_config["name"]
            try:
                body = call_config.get("body")
                file_data = body["file"]

                # Handle different file formats (Ouro vs mock GGen files)
                if "id" in file_data:
                    asset_id = file_data["id"]
                else:
                    raise ValueError(f"File data missing 'id': {file_data}")

                response = self.ouro.routes.use(
                    route_name,
                    input_asset={"assetId": str(asset_id), "assetType": "file"},
                    output={"team_id": self.team_id},
                    timeout=900,
                )
                return route_name, response
            except Exception as exc:  # noqa: BLE001
                return route_name, {"error": str(exc)}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_name = {executor.submit(invoke, c): c["name"] for c in calls}
            for future in as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    route_name, response = future.result()
                    if isinstance(response, dict) and "error" in response:
                        errors[route_name] = response["error"]
                    else:
                        results[route_name] = response
                except Exception as exc:  # noqa: PERF203,BLE001
                    errors[name] = str(exc)

        elapsed = time.time() - start_time
        print(f"Parallel route calls completed in {elapsed:.2f}s")
        if errors:
            # Stop immediately instead of silently defaulting
            raise RuntimeError(f"One or more route calls failed: {errors}")

        # Extract properties from successful route calls
        curie_temp = results["hermes/post-magnetism-curie-temperature"]["temperature"]
        magnetic_density = results["hermes/post-magnetism-magnetic-saturation"][
            "magnetisation_density"
        ]["value"]
        cost = results["hermes/post-structure-cost"]["cost_per_kg"]["value"]
        e_hull = results["mmoderwell/post-materials-thermo-ehull"]["e_above_hull"]
        imaginary_modes_detected = results[
            "mmoderwell/post-materials-phonons-dispersion"
        ]["imaginary_modes_detected"]
        dynamic_stability = not bool(imaginary_modes_detected)

        props = MaterialProperties(
            curie_temperature=curie_temp,
            magnetic_density=magnetic_density,
            cost=cost,
            e_hull=e_hull,
            dynamic_stability=dynamic_stability,
            space_group=material.resolved_space_group,
            num_atoms=material.num_atoms,
        )

        # Capture any returned files as artifacts for later use
        material.artifacts = results

        # Store material in registry for future mutations
        self.material_registry[material.material_id] = material

        # Cache properties by file id and set predicted_properties
        if file_id:
            self._properties_cache[file_id] = props
        material.predicted_properties = props.__dict__

        return props

    def generate_structure_ggen(
        self, composition: str, space_group: str = None, constraints: Dict = None
    ) -> Material:
        """Generate crystal structure using GGen instead of Ouro crystal generator"""

        print(
            f"Generating structure with GGen for {composition} with space group {space_group}"
        )

        # Track requested space group from the agent
        requested_space_group: Optional[int] = None
        if space_group is not None:
            try:
                requested_space_group = int(space_group)
            except Exception:
                requested_space_group = None

        # Use GGen to generate structure
        try:
            result = self.ggen.generate_crystal(
                formula=composition,
                space_group=requested_space_group,
                num_trials=40,
                optimize_geometry=True,  # Enable geometry optimization for better structures
            )

            structure = self.ggen.get_structure()
            cif_content = result.get("cif_content", "")

        except Exception as e:
            print(f"GGen generation failed: {e}")
            # Fall back to Ouro generation if GGen fails
            return self.generate_structure(
                composition, space_group, constraints, use_ggen=False
            )

        # Create a mock file object for compatibility with existing code
        timestamp = datetime.now().isoformat()
        mock_file = {
            "id": f"ggen_{composition}_{timestamp}",
            "name": f"{composition}_SG{requested_space_group or 'auto'}_ggen_{timestamp}",
            "description": f"GGen-generated crystal structure for {composition}",
            "metadata": {
                "composition": composition,
                "generation_method": "ggen",
                "space_group": requested_space_group,
                "num_atoms": len(structure),
                "timestamp": timestamp,
            },
        }

        # Determine resolved space group
        try:
            sga = SpacegroupAnalyzer(structure, symprec=SYMPREC)
            resolved_space_group = int(sga.get_space_group_number())
        except Exception:
            resolved_space_group = None

        material = Material(
            composition=composition,
            atoms=structure,
            num_atoms=len(structure),
            cif_string=cif_content,
            file=mock_file,
            predicted_properties={},
            requested_space_group=requested_space_group,
            used_space_group=requested_space_group,
            resolved_space_group=resolved_space_group,
            generation_method="from_scratch",
        )

        self.material_registry[material.material_id] = material
        return material

    def mutate_material(
        self,
        material: Material,
        operations: List[Dict[str, Any]],
    ) -> Material:
        """Apply multiple mutations to parent material using GGen's mutate_crystal method"""

        print(f"Applying multiple mutations to {material.composition}")
        print(f"Operations: {[op.get('op', 'unknown') for op in operations]}")

        # Set the parent structure in GGen
        self.ggen.set_structure(material.atoms, add_to_trajectory=True)

        mutation_record = MutationRecord(
            mutation_type="multiple_mutations",
            parameters={"operations": operations},
            timestamp=datetime.now(),
            parent_composition=material.composition,
            child_composition="",  # Will be filled after mutation
        )

        try:
            # Apply multiple mutations using GGen's mutate_crystal method
            self.ggen.mutate_crystal(
                operations=operations, repair=True, min_distance=0.8
            )

            # Get mutated structure
            mutated_structure = self.ggen.get_structure()

            # Optimize geometry after mutations
            try:
                print(f"Optimizing geometry for multi-mutated structure...")
                self.ggen.optimize_geometry()
                optimized_structure = self.ggen.get_structure()
                print(f"✓ Multi-mutation geometry optimization completed")
                mutated_structure = optimized_structure
            except Exception as e:
                print(f"⚠️  Multi-mutation optimization failed: {e}")
                print("   Using unoptimized mutated structure")

            # Check similarity between parent and mutated structure
            similarity_score = self._similarity_score(material.atoms)
            print(f"Multi-mutation similarity score: {similarity_score:.3f}")

            new_composition = mutated_structure.composition.reduced_formula

            # Determine space group of mutated structure
            try:
                sga = SpacegroupAnalyzer(mutated_structure, symprec=SYMPREC)
                resolved_space_group = int(sga.get_space_group_number())
            except Exception:
                resolved_space_group = None

            # Create CIF content
            from pymatgen.io.cif import CifWriter

            cif_writer = CifWriter(mutated_structure)
            cif_content = str(cif_writer)

            # Create mock file for mutated structure
            timestamp = datetime.now().isoformat()
            mock_file = {
                "id": f"ggen_multi_mut_{new_composition}_{timestamp}",
                "name": f"{new_composition}_multi_mutated_{timestamp}",
                "description": f"GGen multi-mutated structure: {new_composition} from {material.composition}",
                "metadata": {
                    "composition": new_composition,
                    "generation_method": "multiple_mutations",
                    "parent_id": material.material_id,
                    "parent_composition": material.composition,
                    "mutation_operations": operations,
                    "num_atoms": len(mutated_structure),
                    "timestamp": timestamp,
                },
            }

            # Update mutation record with similarity information
            mutation_record.child_composition = new_composition
            mutation_record.success = True

            # Add similarity information to mutation record
            mutation_record.property_changes = {
                "similarity_score": similarity_score,
                "mutation_effective": similarity_score <= 0.95,
                "num_operations": len(operations),
                "operation_types": [op.get("op", "unknown") for op in operations],
            }

            # Create mutated material
            mutated_material = Material(
                composition=new_composition,
                atoms=mutated_structure,
                num_atoms=len(mutated_structure),
                cif_string=cif_content,
                file=mock_file,
                predicted_properties={},
                resolved_space_group=resolved_space_group,
                generation_method="multiple_mutations",
                parent_material_id=material.material_id,
                mutation_history=material.mutation_history + [mutation_record],
            )

            self.material_registry[mutated_material.material_id] = mutated_material
            return mutated_material

        except Exception as e:
            mutation_record.success = False
            mutation_record.error_message = str(e)
            print(f"Multi-mutation failed: {e}")

            # Return a copy of parent with failed mutation record
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

    def get_mutation_history_summary(self) -> List[Dict[str, Any]]:
        """Get summary of all mutations and their effects"""
        history = []

        for material in self.material_registry.values():
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

    def get_trajectory_visualization(self) -> Optional[Dict[str, Any]]:
        """Get trajectory visualization by uploading trajectory to Ouro and using matterviz route"""
        try:
            # Check if GGen has trajectory data
            if not hasattr(self.ggen, "get_trajectory"):
                print("⚠️  Trajectory functionality not available in GGen")
                return None

            full_trajectory = self.ggen.get_trajectory()
            if not full_trajectory or len(full_trajectory) < 2:
                print("⚠️  No trajectory data available (need at least 2 frames)")
                return None

            print(
                f"📊 Generating trajectory visualization from {len(full_trajectory)} frames"
            )

            # Export trajectory file
            traj_filename = self.ggen.export_trajectory()

            if not traj_filename or not os.path.exists(traj_filename):
                print("⚠️  Trajectory file not found or empty")
                return None

            # Read the traj file content with proper encoding handling
            try:
                with open(traj_filename, "r", encoding="utf-8") as f:
                    xyz_content = f.read()
            except UnicodeDecodeError:
                # Fallback to latin-1 encoding if UTF-8 fails
                try:
                    with open(traj_filename, "r", encoding="latin-1") as f:
                        xyz_content = f.read()
                except Exception as e:
                    print(f"⚠️  Failed to read trajectory file with any encoding: {e}")
                    return None
            except Exception as e:
                print(f"⚠️  Failed to read trajectory file: {e}")
                return None

            # Upload trajectory file to Ouro
            trajectory_file = self.ouro.files.create(
                file_path=traj_filename,
                name=f"mutation_trajectory_{int(time.time())}.traj",
                description="GGen mutation trajectory for visualization",
                visibility=self.visibility,
                team_id=self.team_id,
                parent_id=self.post_id,
            )

            print(f"✓ Uploaded trajectory file to Ouro: {trajectory_file.id}")

            # Use the matterviz trajectory route to generate visualization
            viz_result = self.ouro.routes.use(
                "mmoderwell/post-matterviz-trajectory",
                input_asset={"assetId": str(trajectory_file.id), "assetType": "file"},
                output={"team_id": self.team_id},
            )

            print(f"✓ Generated trajectory visualization")

            # Clean up temporary .traj file
            try:
                os.unlink(traj_filename)
            except Exception as e:
                print(f"⚠️  Failed to clean up temporary file {traj_filename}: {e}")

            return {
                "trajectory_file": (
                    trajectory_file.model_dump(mode="json")
                    if hasattr(trajectory_file, "model_dump")
                    else trajectory_file
                ),
                "visualization": viz_result,
                "frame_count": len(full_trajectory),
            }

        except Exception as e:
            import traceback

            traceback.print_exc()
            print(f"⚠️  Failed to generate trajectory visualization: {e}")
            return None

    def get_mutation_effectiveness_stats(self) -> Dict[str, Any]:
        """Get statistics about mutation effectiveness"""
        stats = {
            "total_mutations": 0,
            "effective_mutations": 0,
            "ineffective_mutations": 0,
            "by_mutation_type": {},
            "average_similarity_scores": {},
            "aggressive_retries_used": 0,
        }

        for material in self.material_registry.values():
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

                # Check if mutation was effective
                if mutation.property_changes:
                    similarity_score = mutation.property_changes.get(
                        "similarity_score", 1.0
                    )
                    is_effective = mutation.property_changes.get(
                        "mutation_effective", False
                    )
                    used_aggressive = mutation.property_changes.get(
                        "aggressive_retry_used", False
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

                    if used_aggressive:
                        stats["aggressive_retries_used"] += 1

                    # Track multiple mutation specific stats
                    if mutation_type == "multiple_mutations":
                        num_operations = mutation.property_changes.get(
                            "num_operations", 0
                        )
                        operation_types = mutation.property_changes.get(
                            "operation_types", []
                        )
                        if "num_operations" not in stats:
                            stats["num_operations"] = []
                        if "operation_types" not in stats:
                            stats["operation_types"] = {}
                        stats["num_operations"].append(num_operations)
                        for op_type in operation_types:
                            if op_type not in stats["operation_types"]:
                                stats["operation_types"][op_type] = 0
                            stats["operation_types"][op_type] += 1

        # Calculate average similarity scores
        for mutation_type, data in stats["by_mutation_type"].items():
            if data["similarity_scores"]:
                stats["average_similarity_scores"][mutation_type] = sum(
                    data["similarity_scores"]
                ) / len(data["similarity_scores"])
            else:
                stats["average_similarity_scores"][mutation_type] = "no_data"

        return stats

    # --- Similarity caching ---
    def _similarity_score(
        self,
        parent_structure: Structure,
    ) -> float:
        """Compute or fetch cached similarity between parent and current GGen structure."""

        result = self.ggen.calculate_similarity(parent_structure)
        return float(result["score"])

    def _generate_rich_filename(self, material: Material) -> str:
        """Generate a descriptive filename using available metadata"""
        composition = material.composition.replace(" ", "_")

        # Add space group information
        sg_info = ""
        if material.resolved_space_group:
            sg_info = f" SG #{material.resolved_space_group}"
        elif material.used_space_group:
            sg_info = f" SG #{material.used_space_group}"

        return f"{composition}{sg_info}"

    def _generate_rich_description(self, material: Material) -> str:
        """Generate a rich description using available metadata"""
        description_parts = []

        # Basic composition and structure info
        description_parts.append(f"Crystal structure for {material.composition}")

        # Space group information
        if material.resolved_space_group:
            description_parts.append(
                f"Space group: {material.resolved_space_group} (resolved from structure)"
            )
        elif material.used_space_group:
            description_parts.append(
                f"Space group: {material.used_space_group} (used in generation)"
            )
        elif material.requested_space_group:
            description_parts.append(
                f"Requested space group: {material.requested_space_group}"
            )

        # Generation method and lineage
        if material.generation_method == "mutation" and material.parent_material_id:
            description_parts.append(
                f"Generated via mutation from parent material {material.parent_material_id}"
            )
            if material.mutation_history:
                last_mutation = material.mutation_history[-1]
                description_parts.append(
                    f"Last mutation: {last_mutation.mutation_type} with parameters {last_mutation.parameters}"
                )
        elif material.generation_method == "from_scratch":
            description_parts.append(
                "Generated from scratch using crystal structure prediction"
            )

        # Structural properties
        description_parts.append(f"Number of atoms: {material.num_atoms}")

        # Predicted properties if available
        if hasattr(material, "predicted_properties") and material.predicted_properties:
            props = material.predicted_properties
            if isinstance(props, dict):
                prop_descriptions = []
                if "curie_temperature" in props:
                    prop_descriptions.append(
                        f"Curie temperature: {props['curie_temperature']:.1f} K"
                    )
                if "magnetic_density" in props:
                    prop_descriptions.append(
                        f"Magnetic density: {props['magnetic_density']:.3f} T"
                    )
                if "cost" in props:
                    prop_descriptions.append(f"Estimated cost: ${props['cost']:.1f}/kg")
                if "e_hull" in props:
                    prop_descriptions.append(
                        f"Energy above hull: {props['e_hull']:.3f} eV/atom"
                    )
                if "dynamic_stability" in props:
                    stability = "stable" if props["dynamic_stability"] else "unstable"
                    prop_descriptions.append(f"Dynamic stability: {stability}")

                if prop_descriptions:
                    description_parts.append(
                        "Predicted properties: " + ", ".join(prop_descriptions)
                    )

        # Generation timestamp
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        description_parts.append(f"Generated: {timestamp}")

        return " | ".join(description_parts)
