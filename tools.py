# ============================================================================
# Computational Tools Interface
# ============================================================================

import dspy
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from typing import List, Dict, Optional, Tuple
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
from models import Material, MaterialProperties

dotenv.load_dotenv(override=True)


class ComputationalTools:
    """Interface to computational materials science tools"""

    def __init__(self):
        self.ouro = Ouro(api_key=os.getenv("OURO_API_KEY"))

    def generate_structure(
        self, composition: str, space_group: str = None, constraints: Dict = None
    ) -> Material:
        """Use Ouro tools to generate crystal structure"""

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
            compatible = self.ouro.routes.use(
                "mmoderwell/get-crystal-gen-compatible-space-groups",
                query={
                    "formula": composition,
                },
            )
            compatible_space_groups = [
                g["number"] for g in compatible["compatible_space_groups"]
            ]
            if requested_space_group not in compatible_space_groups:
                print(
                    f"Space group {requested_space_group} is not compatible with {composition}"
                )
                used_space_group = int(np.random.choice(compatible_space_groups))
            else:
                used_space_group = requested_space_group

        response = self.ouro.routes.use(
            "mmoderwell/post-crystal-gen-generate",
            body={
                "formula": composition,
                "space_group": used_space_group,
                "num_crystals": 10,
                "optimize_geometry": True,
            },
            output={
                "team_id": os.getenv("OURO_TEAM_ID"),
            },
        )
        file = self.ouro.files.retrieve(response["file"]["id"])
        file_data = file.read_data()
        # CIF string
        structure_data = requests.get(file_data.url).text
        atoms = CifParser(StringIO(structure_data)).parse_structures(primitive=False)[0]

        # Determine the resolved space group from the generated structure
        try:
            sga = SpacegroupAnalyzer(atoms, symprec=1e-3)
            resolved_space_group = int(sga.get_space_group_number())
        except Exception:  # noqa: BLE001
            resolved_space_group = None

        # Placeholder return
        return Material(
            composition=composition,
            atoms=atoms,
            num_atoms=len(atoms),
            cif_string=structure_data,
            file=file.model_dump(mode="json"),
            predicted_properties={},
            requested_space_group=requested_space_group,
            used_space_group=used_space_group,
            resolved_space_group=resolved_space_group,
        )

    def evaluate_material_properties(self, material: Material) -> MaterialProperties:
        """Evaluate magnetic properties using computational tools"""

        print(f"Evaluating material properties for {material.composition}")
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
        output_overrides = {"team_id": os.getenv("OURO_TEAM_ID")}
        start_time = time.time()
        results = {}
        errors = {}
        max_workers = min(8, len(calls)) if len(calls) > 0 else 1

        def invoke(call_config: Dict) -> Tuple[str, Dict]:
            route_name = call_config["name"]
            try:
                body = call_config.get("body")
                response = self.ouro.routes.use(
                    route_name,
                    input_asset={"assetId": body["file"]["id"], "assetType": "file"},
                    # body={"file": {**body["file"]["metadata"], **body["file"]}},
                    output=output_overrides,
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
            print(f"One or more route calls failed: {errors}")

        # Placeholder calculation
        props = MaterialProperties(
            curie_temperature=results["hermes/post-magnetism-curie-temperature"][
                "temperature"
            ],
            magnetic_density=results["hermes/post-magnetism-magnetic-saturation"][
                "magnetisation_density"
            ]["value"],
            cost=results["hermes/post-structure-cost"]["cost_per_kg"]["value"],
            e_hull=results["mmoderwell/post-materials-thermo-ehull"]["e_above_hull"],
            dynamic_stability=not results[
                "mmoderwell/post-materials-phonons-dispersion"
            ]["imaginary_modes_detected"],
        )

        # Capture any returned files as artifacts for later use
        material.artifacts = results
        return props
