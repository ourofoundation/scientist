"""Material property evaluation using computational tools.

Two-tier evaluation:
- Tier 1: Fast/cheap routes (cost, Curie temp, magnetic density). Local GGen
  values for e_hull and dynamic stability are preferred when already known.
- Tier 2: Expensive routes (MAE) — only run if tier 1 meets thresholds.
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


# Tier 1 routes that always need Ouro (magnetic/cost — GGen cannot compute these)
TIER1_OURO_ROUTES = {
    "cost": {
        "route": "hermes/estimate-raw-material-cost-per-kg",
        "extract": lambda r: r["cost_per_kg"]["value"],
    },
    "curie_temperature": {
        "route": "hermes/predict-curie-temperature-from-a-cif",
        "extract": lambda r: r["temperature"],
    },
    "magnetic_density": {
        "route": "hermes/estimate-magnetic-moments-and-ms-from-a-cif",
        "extract": lambda r: r["saturation_magnetization"]["tesla"]["value"],
    },
}

# Optional Ouro fallbacks when GGen did not provide local values
TIER1_STABILITY_ROUTES = {
    "e_hull": {
        "route": "mmoderwell/calculate-energy-above-hull",
        "extract": lambda r: r["e_above_hull"],
    },
    "dynamic_stability": {
        "route": "mmoderwell/calculate-phonon-dispersion-and-return-band-structure-plot",
        "extract": lambda r: not bool(r["imaginary_modes_detected"]),
    },
}

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


@dataclass
class Tier2Thresholds:
    """Thresholds that must be met for tier 2 evaluation."""

    dynamic_stability: bool = True
    magnetic_density_min: float = 0.10
    e_hull_max: float = 0.150
    space_group_min: int = 8

    def check(
        self, material: Material, tier1_props: Dict[str, Any]
    ) -> Tuple[bool, List[str]]:
        reasons = []

        dyn_stab = tier1_props.get("dynamic_stability")
        if dyn_stab is None:
            # Unknown phonon status — don't block MAE solely on this
            pass
        elif self.dynamic_stability and not dyn_stab:
            reasons.append("dynamic_stability is False (required: True)")

        mag_dens = tier1_props.get("magnetic_density")
        if mag_dens is None:
            reasons.append("magnetic_density not evaluated")
        elif mag_dens < self.magnetic_density_min:
            reasons.append(
                f"magnetic_density={mag_dens:.2f} < {self.magnetic_density_min}"
            )

        e_hull = tier1_props.get("e_hull")
        if e_hull is None:
            reasons.append("e_hull not evaluated")
        elif e_hull > self.e_hull_max:
            reasons.append(f"e_hull={e_hull:.3f} > {self.e_hull_max}")

        space_group = material.resolved_space_group
        if space_group is None:
            reasons.append("space_group not available")
        elif space_group < self.space_group_min:
            reasons.append(f"space_group={space_group} < {self.space_group_min}")

        return len(reasons) == 0, reasons


DEFAULT_TIER2_THRESHOLDS = Tier2Thresholds()


@dataclass
class Interpretation:
    """Structured interpretation of evaluation results."""

    analysis: str = ""
    insights: str = ""


class MaterialEvaluator:
    """Evaluates material properties using GGen locals + Ouro routes."""

    def __init__(
        self,
        ouro_client: OuroClient,
        registry: MaterialRegistry,
        tier2_thresholds: Optional[Tier2Thresholds] = None,
    ) -> None:
        self.ouro = ouro_client
        self.registry = registry
        self.tier2_thresholds = tier2_thresholds or DEFAULT_TIER2_THRESHOLDS

    def evaluate_properties(self, material: Material) -> MaterialProperties:
        """Evaluate material properties using two-tier computational routes."""
        logger.info(f"Evaluating properties for {material.composition}")

        file_id = self._ensure_ouro_file(material)
        if file_id is None:
            logger.error(f"Cannot evaluate {material.composition}: no file available")
            return self._create_empty_properties(material)

        cached = self.registry.get_cached_properties(file_id)
        if cached:
            logger.debug(f"Using cached properties for {material.composition}")
            material.predicted_properties = cached.__dict__
            return cached

        # Seed from GGen-local values already on the material
        local = material.predicted_properties or {}
        seeded: Dict[str, Any] = {}
        if local.get("e_hull") is not None:
            seeded["e_hull"] = local["e_hull"]
            logger.info(f"Using GGen e_hull={seeded['e_hull']:.4f}")
        if local.get("dynamic_stability") is not None:
            seeded["dynamic_stability"] = local["dynamic_stability"]
            logger.info(f"Using GGen dynamic_stability={seeded['dynamic_stability']}")

        # Tier 1 Ouro routes (magnetic + cost)
        logger.info("Running tier 1 evaluation (magnetic/cost routes)")
        tier1_results, tier1_errors = self._execute_routes(file_id, TIER1_OURO_ROUTES)
        tier1_extracted = self._extract_route_values(
            tier1_results, tier1_errors, TIER1_OURO_ROUTES
        )
        tier1_extracted.update(seeded)

        # Fallback stability routes only if GGen didn't provide them
        missing_stability = {
            k: v
            for k, v in TIER1_STABILITY_ROUTES.items()
            if k not in seeded
        }
        if missing_stability:
            logger.info(
                f"Fetching missing stability props via Ouro: {list(missing_stability)}"
            )
            stab_results, stab_errors = self._execute_routes(
                file_id, missing_stability
            )
            stab_extracted = self._extract_route_values(
                stab_results, stab_errors, missing_stability
            )
            tier1_extracted.update(stab_extracted)
            tier1_results.update(stab_results)
            tier1_errors.update(stab_errors)

        logger.info(
            f"Tier 1 evaluated: "
            f"{[k for k, v in tier1_extracted.items() if v is not None]}"
        )

        # Tier 2: conditional MAE
        tier2_results: Dict[str, Any] = {}
        tier2_errors: Dict[str, str] = {}
        tier2_extracted: Dict[str, Any] = {}

        passed, reasons = self.tier2_thresholds.check(material, tier1_extracted)
        if passed:
            logger.info("Tier 1 thresholds met — running tier 2 (MAE)")
            tier2_results, tier2_errors = self._execute_routes(file_id, TIER2_ROUTES)
            tier2_extracted = self._extract_route_values(
                tier2_results, tier2_errors, TIER2_ROUTES
            )
        else:
            logger.info(f"Tier 2 skipped — thresholds not met: {reasons}")
            for prop_name in TIER2_ROUTES:
                tier2_extracted[prop_name] = None

        all_results = {**tier1_results, **tier2_results}
        all_errors = {**tier1_errors, **tier2_errors}
        all_extracted = {**tier1_extracted, **tier2_extracted}

        props = self._build_properties(material, all_extracted, all_errors)

        successful = [
            k
            for k, v in props.__dict__.items()
            if v is not None
            and k not in ("evaluation_errors", "space_group", "num_atoms")
        ]
        logger.info(f"Evaluated: {successful}")
        if props.evaluation_errors:
            logger.warning(f"Failed: {list(props.evaluation_errors.keys())}")

        material.artifacts = all_results
        self.registry.cache_properties(file_id, props)
        self.registry.register(material)
        material.predicted_properties = props.__dict__

        return props

    def _ensure_ouro_file(self, material: Material) -> Optional[str]:
        if not isinstance(material.file, dict):
            return None

        file_id = material.file.get("id", "")

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
        routes = [
            (name, cfg["route"], cfg.get("body")) for name, cfg in routes_config.items()
        ]
        results: Dict[str, Any] = {}
        errors: Dict[str, str] = {}

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

        logger.debug(f"Route calls completed in {time.time() - start_time:.2f}s")
        return results, errors

    def _extract_route_values(
        self,
        results: Dict[str, Any],
        errors: Dict[str, str],
        routes_config: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        extracted = {}
        for prop_name, config in routes_config.items():
            if prop_name in errors or prop_name not in results:
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
        return MaterialProperties(
            space_group=material.resolved_space_group,
            num_atoms=material.num_atoms,
            evaluation_errors={"file": "No valid file available"},
        )

    def _generate_filename(self, material: Material) -> str:
        composition = material.composition.replace(" ", "_")
        sg = material.resolved_space_group or material.used_space_group
        sg_info = f" SG #{sg}" if sg else ""
        return f"{composition}{sg_info}"

    def _generate_description(self, material: Material) -> str:
        parts = [f"Crystal structure for {material.composition}"]
        if material.chemical_system:
            parts.append(f"System: {material.chemical_system}")
        if material.resolved_space_group:
            parts.append(f"Space group: {material.resolved_space_group}")
        parts.append(f"Atoms: {material.num_atoms}")
        return " | ".join(parts)
