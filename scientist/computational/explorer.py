"""Hosted GGen chemical-system exploration via Ouro routes."""

from __future__ import annotations

import json
from collections import Counter
from io import StringIO
from typing import Any, Dict, List, Optional, Tuple

from pymatgen.io.cif import CifParser

from ..data.models import Material, ExplorationSummary
from ..utils.logging import get_logger
from .ouro_client import OuroClient
from .registry import MaterialRegistry

logger = get_logger("explorer")

_CRYSTAL_SYSTEMS = [
    (2, "triclinic"),
    (15, "monoclinic"),
    (74, "orthorhombic"),
    (142, "tetragonal"),
    (167, "trigonal"),
    (194, "hexagonal"),
    (230, "cubic"),
]


def _crystal_system(sg: Optional[int]) -> str:
    if sg is None:
        return "unknown"
    for upper, name in _CRYSTAL_SYSTEMS:
        if sg <= upper:
            return name
    return "unknown"


class SystemExplorer:
    """Explore chemical systems using Ouro-hosted GGen routes."""

    def __init__(
        self,
        ouro_client: OuroClient,
        registry: MaterialRegistry,
        max_atoms: int = 16,
        min_atoms: int = 2,
        num_trials: int = 10,
        e_hull_cutoff: float = 0.15,
        max_candidates: int = 5,
        max_stoichiometries: int = 100,
        poll_timeout: int = 60 * 60 * 4,
    ) -> None:
        self.ouro = ouro_client
        self.registry = registry
        self.max_atoms = max_atoms
        self.min_atoms = min_atoms
        self.num_trials = num_trials
        self.e_hull_cutoff = e_hull_cutoff
        self.max_candidates = max_candidates
        self.max_stoichiometries = max_stoichiometries
        self.poll_timeout = poll_timeout

    def explore(
        self,
        chemical_system: str,
        crystal_systems: Optional[List[str]] = None,
        min_fraction: Optional[Dict[str, float]] = None,
        max_fraction: Optional[Dict[str, float]] = None,
    ) -> Tuple[List[Material], ExplorationSummary]:
        """Explore a chemical system on hosted GGen; return near-hull Materials."""
        chemical_system = self._normalize_system(chemical_system)
        logger.info(f"Exploring chemical system via Ouro GGen: {chemical_system}")

        result = self.ouro.explore_chemical_system(
            system=chemical_system,
            max_atoms=self.max_atoms,
            min_atoms=self.min_atoms,
            num_trials=self.num_trials,
            e_above_hull=self.e_hull_cutoff,
            max_stoichiometries=self.max_stoichiometries,
            crystal_systems=crystal_systems,
            min_fraction=min_fraction,
            max_fraction=max_fraction,
            skip_existing=False,
            poll_timeout=self.poll_timeout,
        )

        summary_data = result.get("summary") or {}
        stable_phases = list(summary_data.get("stable_phases") or [])

        # Prefer CIFs from the explore output; fall back to export route
        candidates = self._load_candidates(
            chemical_system=chemical_system,
            stable_phases=stable_phases,
            candidate_cifs_asset=result.get("candidate_cifs"),
            crystal_systems=crystal_systems,
        )

        # Prefer dynamically stable when known; sort by e_hull
        candidates.sort(
            key=lambda c: (
                0
                if c.get("is_dynamically_stable")
                else 1
                if c.get("is_dynamically_stable") is None
                else 2,
                c.get("e_above_hull")
                if c.get("e_above_hull") is not None
                else float("inf"),
            )
        )
        survivors = candidates[: self.max_candidates]

        materials: List[Material] = []
        for cand in survivors:
            material = self._candidate_to_material(cand, chemical_system)
            if material is not None:
                materials.append(material)

        summary = self._build_summary(
            summary_data, candidates, materials, chemical_system
        )
        logger.info(
            f"{chemical_system}: {summary.num_near_hull} near-hull, "
            f"evaluating {len(materials)}"
        )
        return materials, summary

    def _load_candidates(
        self,
        chemical_system: str,
        stable_phases: List[Dict[str, Any]],
        candidate_cifs_asset: Optional[Dict[str, Any]],
        crystal_systems: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        """Build candidate dicts with CIF content from explore zip or export."""
        phase_by_formula = {
            p.get("formula"): p for p in stable_phases if p.get("formula")
        }

        cifs: Dict[str, str] = {}
        structures_meta: List[Dict[str, Any]] = []

        if candidate_cifs_asset and candidate_cifs_asset.get("id"):
            try:
                parsed = self.ouro.parse_candidate_zip(candidate_cifs_asset["id"])
                cifs = parsed["cifs"]
                structures_meta = parsed["structures"]
            except Exception as e:
                logger.warning(f"Failed to parse explore CIF zip: {e}")

        if not cifs:
            try:
                exported = self.ouro.export_candidates(
                    system=chemical_system,
                    max_e_above_hull=self.e_hull_cutoff,
                    crystal_systems=crystal_systems,
                    dynamically_stable_only=False,
                )
                cifs = exported["cifs"]
                structures_meta = exported["structures"]
            except Exception as e:
                logger.warning(f"Export fallback failed: {e}")

        # Index metadata by filename when available
        meta_by_file = {
            m["filename"]: m for m in structures_meta if m.get("filename")
        }

        candidates: List[Dict[str, Any]] = []
        for path, cif_content in cifs.items():
            filename = path.split("/")[-1]
            meta = meta_by_file.get(filename, {})
            formula = meta.get("formula") or self._formula_from_cif_name(filename)

            phase = phase_by_formula.get(formula, {})
            candidates.append(
                {
                    "formula": formula or filename,
                    "cif_content": cif_content,
                    "e_above_hull": meta.get("e_above_hull", phase.get("e_above_hull")),
                    "energy_per_atom": meta.get(
                        "energy_per_atom", phase.get("energy_per_atom")
                    ),
                    "space_group_number": meta.get(
                        "space_group_number", phase.get("space_group_number")
                    ),
                    "space_group_symbol": meta.get(
                        "space_group_symbol", phase.get("space_group")
                    ),
                    "is_dynamically_stable": meta.get(
                        "is_dynamically_stable", phase.get("is_dynamically_stable")
                    ),
                    "num_atoms": meta.get("num_atoms", phase.get("num_atoms")),
                    "is_on_hull": meta.get("is_on_hull", phase.get("is_on_hull")),
                }
            )

        # If we have phase metadata but no CIFs, still surface them for the summary
        if not candidates and stable_phases:
            logger.warning(
                "No CIF content available; summary will list phases without structures"
            )
            for phase in stable_phases:
                candidates.append({**phase, "cif_content": None})

        return candidates

    def _candidate_to_material(
        self, candidate: Dict[str, Any], chemical_system: str
    ) -> Optional[Material]:
        cif_content = candidate.get("cif_content")
        if not cif_content:
            logger.warning(
                f"No CIF for {candidate.get('formula')}, skipping evaluation"
            )
            return None

        try:
            structure = CifParser(StringIO(cif_content)).parse_structures(
                primitive=False
            )[0]
        except Exception as e:
            logger.warning(f"Failed to parse CIF for {candidate.get('formula')}: {e}")
            return None

        formula = candidate.get("formula") or structure.composition.reduced_formula
        sg = candidate.get("space_group_number")
        e_hull = candidate.get("e_above_hull")
        num_atoms = candidate.get("num_atoms") or len(structure)

        predicted = {
            "e_hull": e_hull,
            "dynamic_stability": candidate.get("is_dynamically_stable"),
            "space_group": sg,
            "num_atoms": num_atoms,
            "energy_per_atom": candidate.get("energy_per_atom"),
        }

        # Upload now so downstream eval doesn't need a mock-file dance
        try:
            uploaded = self.ouro.upload_cif_content(
                cif_content,
                name=f"{formula} SG#{sg}" if sg else formula,
                description=(
                    f"GGen-explored {formula} in {chemical_system} "
                    f"(E_hull={e_hull})"
                ),
            )
            file_dict = uploaded.model_dump(mode="json")
        except Exception as e:
            logger.warning(f"Upload failed for {formula}, using placeholder: {e}")
            file_dict = {
                "id": f"ggen_{formula}",
                "name": formula,
                "description": f"GGen-explored {formula}",
            }

        material = Material(
            composition=formula,
            atoms=structure,
            num_atoms=num_atoms,
            cif_string=cif_content,
            file=file_dict,
            predicted_properties=predicted,
            requested_space_group=sg,
            used_space_group=sg,
            resolved_space_group=sg,
            generation_method="exploration",
            chemical_system=chemical_system,
        )
        self.registry.register(material)
        return material

    def _build_summary(
        self,
        summary_data: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        materials: List[Material],
        chemical_system: str,
    ) -> ExplorationSummary:
        crystal_counts: Counter = Counter()
        for c in candidates:
            crystal_counts[_crystal_system(c.get("space_group_number"))] += 1

        best = []
        for c in candidates[:10]:
            best.append(
                {
                    "formula": c.get("formula"),
                    "e_hull": c.get("e_above_hull"),
                    "space_group": c.get("space_group_number"),
                    "space_group_symbol": c.get("space_group_symbol"),
                    "crystal_system": _crystal_system(c.get("space_group_number")),
                    "num_atoms": c.get("num_atoms"),
                    "dynamically_stable": c.get("is_dynamically_stable"),
                    "selected_for_evaluation": any(
                        m.composition == c.get("formula") for m in materials
                    ),
                }
            )

        on_hull = sum(
            1
            for c in candidates
            if (c.get("e_above_hull") is not None and c["e_above_hull"] <= 1e-6)
            or c.get("is_on_hull")
        )

        return ExplorationSummary(
            chemical_system=chemical_system,
            num_candidates=int(summary_data.get("num_candidates") or len(candidates)),
            num_successful=int(
                summary_data.get("num_successful")
                or summary_data.get("num_candidates")
                or len(candidates)
            ),
            num_on_hull=on_hull,
            num_near_hull=len(candidates),
            num_evaluated=len(materials),
            crystal_system_counts=dict(crystal_counts),
            best_candidates=best,
            time_seconds=float(summary_data.get("total_time_seconds") or 0.0),
        )

    @staticmethod
    def _formula_from_cif_name(filename: str) -> Optional[str]:
        # e.g. 001_Fe2Co_12meV.cif → Fe2Co
        stem = filename.rsplit(".", 1)[0]
        parts = stem.split("_")
        if len(parts) >= 2:
            return parts[1]
        return None

    @staticmethod
    def _normalize_system(system: str) -> str:
        cleaned = (
            system.replace(",", "-").replace(" ", "-").replace("_", "-").strip("-")
        )
        elements = [e.strip() for e in cleaned.split("-") if e.strip()]
        elements = [e[0].upper() + e[1:].lower() if e else e for e in elements]
        return "-".join(sorted(set(elements)))

    @staticmethod
    def parse_crystal_systems(raw: str) -> Optional[List[str]]:
        if not raw or raw.strip().lower() in {"all", "none", "any", ""}:
            return None
        valid = {
            "triclinic",
            "monoclinic",
            "orthorhombic",
            "tetragonal",
            "trigonal",
            "hexagonal",
            "cubic",
        }
        parts = [p.strip().lower() for p in raw.replace(";", ",").split(",")]
        systems = [p for p in parts if p in valid]
        return systems or None

    @staticmethod
    def parse_fraction_json(raw: str) -> Optional[Dict[str, float]]:
        if not raw or raw.strip() in {"{}", "null", "none", "None", ""}:
            return None
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data:
                return {str(k): float(v) for k, v in data.items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning(f"Could not parse fraction JSON: {raw!r}")
        return None
