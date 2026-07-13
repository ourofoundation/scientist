"""Material property evaluation using computational tools.

Implements two-tier evaluation:
- Tier 1: Fast/cheap routes (cost, curie temp, magnetic density, e_hull, dynamic stability)
- Tier 2: Expensive routes (MAE) - only run if tier 1 meets thresholds
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple, List

from ..data.models import Material, MaterialProperties
from ..utils.logging import get_logger
from .ouro_client import OuroClient
from .registry import MaterialRegistry

logger = get_logger("evaluator")


# Tier 1 routes - fast/cheap, always run
TIER1_ROUTES = {
    "cost": {
        "route": "hermes/calculate-the-estimated-raw-material-cost-per-kg",
        "extract": lambda r: r["cost_per_kg"]["value"],
    },
    "curie_temperature": {
        "route": "hermes/predict-the-curie-temperature-of-a-material",
        "extract": lambda r: r["temperature"],
    },
    "magnetic_density": {
        "route": "hermes/calculate-magnetic-saturation-and-related-properties",
        "extract": lambda r: r["magnetisation_density"]["value"],
    },
    "e_hull": {
        "route": "mmoderwell/calculate-energy-above-hull",
        "extract": lambda r: r["e_above_hull"],
    },
    "dynamic_stability": {
        "route": "mmoderwell/calculate-phonon-dispersion-and-return-band-structure-plot",
        "extract": lambda r: not bool(r["imaginary_modes_detected"]),
    },
}

# Tier 2 routes - expensive, only run if tier 1 thresholds are met
TIER2_ROUTES = {
    "magnetic_anisotropy_energy": {
        "route": "mmoderwell/calculate-magnetic-anisotropy-energy-mae",
        "extract": lambda r: r["mae_mj_per_m3"],
        "body": {
            "method": "tb2j",
            "ecutwfc": 65,
            "scf_thr": 0.000001,
            "kspacing": 0.16,
            "scf_nmax": 200,
            "smearing_sigma": 0.05,
            "smearing_method": "mp",
        },
    },
}

# Combined for backwards compatibility
PROPERTY_ROUTES = {**TIER1_ROUTES, **TIER2_ROUTES}


@dataclass
class Tier2Thresholds:
    """Thresholds that must be met for tier 2 evaluation."""

    dynamic_stability: bool = True  # Must be dynamically stable
    magnetic_density_min: float = 0.10  # Must have magnetic density > 1
    e_hull_max: float = 0.150  # e_hull must be below this
    space_group_min: int = 8  # space group must be above this

    def check(
        self, material: Material, tier1_props: Dict[str, Any]
    ) -> Tuple[bool, List[str]]:
        """Check if tier 1 properties meet thresholds for tier 2.

        Args:
            material: Material object (needed for space group check)
            tier1_props: Extracted tier 1 property values

        Returns:
            Tuple of (passed, reasons) where reasons lists failed checks
        """
        reasons = []

        # Check dynamic stability
        dyn_stab = tier1_props.get("dynamic_stability")
        if dyn_stab is None:
            reasons.append("dynamic_stability not evaluated")
        elif self.dynamic_stability and not dyn_stab:
            reasons.append(f"dynamic_stability is False (required: True)")

        # Check magnetic density
        mag_dens = tier1_props.get("magnetic_density")
        if mag_dens is None:
            reasons.append("magnetic_density not evaluated")
        elif mag_dens < self.magnetic_density_min:
            reasons.append(
                f"magnetic_density={mag_dens:.2f} < {self.magnetic_density_min}"
            )

        # Check e_hull
        e_hull = tier1_props.get("e_hull")
        if e_hull is None:
            reasons.append("e_hull not evaluated")
        elif e_hull > self.e_hull_max:
            reasons.append(f"e_hull={e_hull:.3f} > {self.e_hull_max}")

        # Check space group
        space_group = material.resolved_space_group
        if space_group is None:
            reasons.append("space_group not available")
        elif space_group < self.space_group_min:
            reasons.append(f"space_group={space_group} < {self.space_group_min}")

        passed = len(reasons) == 0
        return passed, reasons


# Default thresholds
DEFAULT_TIER2_THRESHOLDS = Tier2Thresholds()


class MaterialEvaluator:
    """Handles evaluation of material properties using computational tools.

    Implements two-tier evaluation:
    - Tier 1: Always runs fast/cheap routes
    - Tier 2: Only runs expensive routes (MAE) if tier 1 meets thresholds
    """

    def __init__(
        self,
        ouro_client: OuroClient,
        registry: MaterialRegistry,
        tier2_thresholds: Optional[Tier2Thresholds] = None,
    ) -> None:
        """Initialize evaluator.

        Args:
            ouro_client: Ouro API client for route calls
            registry: Material registry for caching
            tier2_thresholds: Thresholds for tier 2 evaluation (default thresholds used if None)
        """
        self.ouro = ouro_client
        self.registry = registry
        self.tier2_thresholds = tier2_thresholds or DEFAULT_TIER2_THRESHOLDS

    def evaluate_properties(self, material: Material) -> MaterialProperties:
        """Evaluate material properties using two-tier computational routes.

        Tier 1 routes always run. Tier 2 routes (MAE) only run if tier 1
        properties meet the configured thresholds.

        Args:
            material: Material to evaluate

        Returns:
            Computed material properties
        """
        logger.info(f"Evaluating properties for {material.composition}")

        # Check for cached properties
        if material.predicted_properties:
            try:
                return MaterialProperties(**material.predicted_properties)
            except Exception:
                pass

        # Ensure material has Ouro file
        file_id = self._ensure_ouro_file(material)
        if file_id is None:
            logger.error(f"Cannot evaluate {material.composition}: no file available")
            return self._create_empty_properties(material)

        # Check cache
        cached = self.registry.get_cached_properties(file_id)
        if cached:
            logger.debug(f"Using cached properties for {material.composition}")
            material.predicted_properties = cached.__dict__
            return cached

        # === TIER 1: Always execute ===
        logger.info("Running tier 1 evaluation (fast routes)")
        tier1_results, tier1_errors = self._execute_routes(file_id, TIER1_ROUTES)
        tier1_extracted = self._extract_route_values(
            tier1_results, tier1_errors, TIER1_ROUTES
        )

        # Log tier 1 results
        tier1_successful = [k for k, v in tier1_extracted.items() if v is not None]
        logger.info(f"Tier 1 evaluated: {tier1_successful}")

        # === TIER 2: Conditional execution ===
        tier2_results = {}
        tier2_errors = {}
        tier2_extracted = {}

        passed, reasons = self.tier2_thresholds.check(material, tier1_extracted)

        if passed:
            logger.info("Tier 1 thresholds met - running tier 2 evaluation (MAE)")
            tier2_results, tier2_errors = self._execute_routes(file_id, TIER2_ROUTES)
            tier2_extracted = self._extract_route_values(
                tier2_results, tier2_errors, TIER2_ROUTES
            )
            tier2_successful = [k for k, v in tier2_extracted.items() if v is not None]
            logger.info(f"Tier 2 evaluated: {tier2_successful}")
        else:
            logger.info(f"Tier 2 skipped - thresholds not met: {reasons}")
            # Mark tier 2 properties as skipped (not errors)
            for prop_name in TIER2_ROUTES:
                tier2_extracted[prop_name] = None

        # Combine results
        all_results = {**tier1_results, **tier2_results}
        all_errors = {**tier1_errors, **tier2_errors}
        all_extracted = {**tier1_extracted, **tier2_extracted}

        # Build properties object
        props = self._build_properties(material, all_extracted, all_errors)

        # Log overall results
        successful = [
            k
            for k, v in props.__dict__.items()
            if v is not None
            and k not in ("evaluation_errors", "space_group", "num_atoms")
        ]
        logger.info(f"Evaluated: {successful}")
        if props.evaluation_errors:
            logger.warning(f"Failed: {list(props.evaluation_errors.keys())}")

        # Cache and store
        material.artifacts = all_results
        self.registry.cache_properties(file_id, props)
        self.registry.register(material)
        material.predicted_properties = props.__dict__

        return props

    def _ensure_ouro_file(self, material: Material) -> Optional[str]:
        """Ensure material has an Ouro file, uploading if needed.

        Args:
            material: Material to check/upload

        Returns:
            File ID if available
        """
        if not isinstance(material.file, dict):
            return None

        file_id = material.file.get("id", "")

        # GGen files need to be uploaded
        if file_id.startswith("ggen_"):
            logger.debug("Uploading GGen structure to Ouro")
            try:
                name = self._generate_filename(material)
                description = self._generate_description(material)
                uploaded = self.ouro.upload_cif_content(
                    material.cif_string, name, description
                )
                material.file = uploaded.model_dump(mode="json")
                return uploaded.id
            except Exception as e:
                logger.error(f"Failed to upload structure: {e}")
                raise

        return file_id if file_id else None

    def _execute_routes(
        self,
        file_id: str,
        routes_config: Dict[str, Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], Dict[str, str]]:
        """Execute property evaluation routes in parallel.

        Args:
            file_id: Ouro file ID
            routes_config: Route configuration dictionary (TIER1_ROUTES or TIER2_ROUTES)

        Returns:
            Tuple of (results, errors) dictionaries
        """
        routes = [
            (name, cfg["route"], cfg.get("body")) for name, cfg in routes_config.items()
        ]
        results = {}
        errors = {}

        if not routes:
            return results, errors

        start_time = time.time()
        max_workers = min(8, len(routes))

        def invoke(
            prop_name: str, route_name: str, body: Optional[Dict[str, Any]] = None
        ) -> Tuple[str, Any]:
            try:
                response = self.ouro.execute_route(route_name, file_id, body=body)
                return prop_name, response
            except Exception as e:
                return prop_name, {"error": str(e)}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(invoke, name, route, body): name
                for name, route, body in routes
            }

            for future in as_completed(futures):
                prop_name = futures[future]
                try:
                    name, response = future.result()
                    if isinstance(response, dict) and "error" in response:
                        errors[name] = response["error"]
                    else:
                        results[name] = response
                except Exception as e:
                    errors[prop_name] = str(e)

        elapsed = time.time() - start_time
        logger.debug(f"Route calls completed in {elapsed:.2f}s")

        return results, errors

    def _extract_route_values(
        self,
        results: Dict[str, Any],
        errors: Dict[str, str],
        routes_config: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Extract property values from route results.

        Args:
            results: Successful route results
            errors: Route errors
            routes_config: Route configuration dictionary

        Returns:
            Dictionary of extracted property values
        """
        extracted = {}

        for prop_name, config in routes_config.items():
            if prop_name in errors:
                extracted[prop_name] = None
                continue

            if prop_name not in results:
                extracted[prop_name] = None
                continue

            try:
                extracted[prop_name] = config["extract"](results[prop_name])
            except (KeyError, TypeError):
                extracted[prop_name] = None

        return extracted

    def _build_properties(
        self,
        material: Material,
        extracted: Dict[str, Any],
        errors: Dict[str, str],
    ) -> MaterialProperties:
        """Build MaterialProperties from extracted values.

        Args:
            material: Source material
            extracted: Extracted property values
            errors: Route errors

        Returns:
            MaterialProperties object
        """
        return MaterialProperties(
            curie_temperature=extracted.get("curie_temperature"),
            magnetic_density=extracted.get("magnetic_density"),
            magnetic_anisotropy_energy=extracted.get("magnetic_anisotropy_energy"),
            cost=extracted.get("cost"),
            e_hull=extracted.get("e_hull"),
            dynamic_stability=extracted.get("dynamic_stability"),
            space_group=material.resolved_space_group,
            num_atoms=material.num_atoms,
            evaluation_errors=errors,
        )

    def _create_empty_properties(self, material: Material) -> MaterialProperties:
        """Create empty properties object with error recorded."""
        return MaterialProperties(
            space_group=material.resolved_space_group,
            num_atoms=material.num_atoms,
            evaluation_errors={"file": "No valid file available"},
        )

    def _generate_filename(self, material: Material) -> str:
        """Generate descriptive filename for material."""
        composition = material.composition.replace(" ", "_")
        sg_info = ""
        if material.resolved_space_group:
            sg_info = f" SG #{material.resolved_space_group}"
        elif material.used_space_group:
            sg_info = f" SG #{material.used_space_group}"
        return f"{composition}{sg_info}"

    def _generate_description(self, material: Material) -> str:
        """Generate description for material file."""
        parts = [f"Crystal structure for {material.composition}"]

        if material.resolved_space_group:
            parts.append(f"Space group: {material.resolved_space_group}")

        if material.generation_method == "mutation" and material.parent_material_id:
            parts.append(f"Mutated from {material.parent_material_id}")

        parts.append(f"Atoms: {material.num_atoms}")

        return " | ".join(parts)

    def interpret_results(
        self,
        material: Material,
        properties: MaterialProperties,
        targets: Dict[str, Any],
        interpretation_module,
    ) -> Any:
        """Interpret computational results and extract insights.

        Args:
            material: The material that was evaluated
            properties: Computed properties
            targets: Target property values
            interpretation_module: DSPy module for interpretation

        Returns:
            Interpretation results with insights and analysis
        """
        import json

        mutation_info = "none"
        parent_props = "none"

        if material.generation_method == "mutation" and material.parent_material_id:
            parent_material = self.registry.get(material.parent_material_id)
            if parent_material and material.mutation_history:
                last_mutation = material.mutation_history[-1]
                mutation_info = f"{last_mutation.mutation_type} with params {last_mutation.parameters}"
                if hasattr(parent_material, "predicted_properties"):
                    parent_props = json.dumps(parent_material.predicted_properties)

        return interpretation_module(
            material=material.to_json(),
            target_properties=json.dumps(targets),
            mutation_applied=mutation_info,
            parent_properties=parent_props,
        )
