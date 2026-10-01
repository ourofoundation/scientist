"""Hosted GGen chemical-system exploration via Ouro routes."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..utils.logging import get_logger

if TYPE_CHECKING:
    from .ouro_client import OuroClient

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
CRYSTAL_SYSTEM_NAMES = [name for _, name in _CRYSTAL_SYSTEMS]


def crystal_system(sg: Optional[int]) -> str:
    if sg is None:
        return "unknown"
    for upper, name in _CRYSTAL_SYSTEMS:
        if sg <= upper:
            return name
    return "unknown"


def normalize_system(system: str) -> str:
    """"fe, co_bi" → "Bi-Co-Fe"."""
    cleaned = system.replace(",", "-").replace(" ", "-").replace("_", "-")
    elements = [e.strip() for e in cleaned.split("-") if e.strip()]
    elements = [e[0].upper() + e[1:].lower() for e in elements]
    return "-".join(sorted(set(elements)))


def reduced_formula(formula: str) -> str:
    """"B4Fe8" → "BFe2": element counts divided by their gcd, elements sorted."""
    counts: Dict[str, int] = {}
    for element, number in re.findall(r"([A-Z][a-z]?)(\d*)", formula or ""):
        counts[element] = counts.get(element, 0) + int(number or 1)
    if not counts:
        return formula
    divisor = math.gcd(*counts.values())
    return "".join(
        f"{el}{n // divisor if n // divisor > 1 else ''}"
        for el, n in sorted(counts.items())
    )


def identity_key(formula: str, properties: Dict[str, Any]) -> str:
    """Fallback identity when the backend does not name equivalent structures.

    The same compound in the same space group at the same energy (to 1 meV)
    is almost always one structure written in different cell sizes.
    """
    e_hull = properties.get("e_hull")
    energy = "?" if e_hull is None else f"{round(e_hull, 3) + 0.0:.3f}"
    return f"{reduced_formula(formula)}|sg{properties.get('space_group')}|{energy}"


@dataclass
class Exploration:
    """What one GGen exploration produced."""

    chemical_system: str
    # Near-hull candidates, most stable first. Each has formula, cif, a
    # `properties` dict seeded with what GGen already computed, and an
    # `identity` shared by candidates that are the same structure.
    candidates: List[Dict[str, Any]]
    summary: Dict[str, Any]
    # Ouro ids worth citing: action_id, report, phase_diagram, candidate_cifs.
    assets: Dict[str, str] = field(default_factory=dict)


class SystemExplorer:
    """Explore chemical systems using Ouro-hosted GGen routes."""

    def __init__(
        self,
        ouro_client: OuroClient,
        max_atoms: int = 16,
        min_atoms: int = 2,
        num_trials: int = 10,
        e_hull_cutoff: float = 0.15,
        max_stoichiometries: int = 100,
        max_candidates: int = 60,
        poll_timeout: int = 60 * 60 * 4,
    ) -> None:
        self.ouro = ouro_client
        self.max_atoms = max_atoms
        self.min_atoms = min_atoms
        self.num_trials = num_trials
        self.e_hull_cutoff = e_hull_cutoff
        self.max_stoichiometries = max_stoichiometries
        self.max_candidates = max_candidates
        self.poll_timeout = poll_timeout

    def explore(
        self,
        chemical_system: str,
        crystal_systems: Optional[List[str]] = None,
        min_fraction: Optional[Dict[str, float]] = None,
        max_fraction: Optional[Dict[str, float]] = None,
        max_atoms: Optional[int] = None,
        num_trials: Optional[int] = None,
        skip_existing: bool = False,
    ) -> Exploration:
        """Explore a chemical system on hosted GGen; return near-hull candidates."""
        chemical_system = normalize_system(chemical_system)
        logger.info(f"Exploring chemical system via Ouro GGen: {chemical_system}")

        result = self.ouro.explore_chemical_system(
            system=chemical_system,
            max_atoms=max_atoms or self.max_atoms,
            min_atoms=self.min_atoms,
            num_trials=num_trials or self.num_trials,
            e_above_hull=self.e_hull_cutoff,
            max_stoichiometries=self.max_stoichiometries,
            crystal_systems=crystal_systems or None,
            min_fraction=min_fraction or None,
            max_fraction=max_fraction or None,
            skip_existing=skip_existing,
            poll_timeout=self.poll_timeout,
        )

        summary_data = result.get("summary") or {}
        # `run_phases` is what this exploration produced under its constraints;
        # `stable_phases` is everything near the hull in GGen's database.
        run_ids = [
            p["structure_id"]
            for p in summary_data.get("run_phases") or []
            if p.get("structure_id")
        ]
        candidates = self._load_candidates(chemical_system, run_ids, crystal_systems)
        candidates = candidates[: self.max_candidates]

        assets = {"action_id": result.get("action_id")}
        for key in ("report", "phase_diagram", "candidate_cifs"):
            asset = result.get(key) or {}
            assets[key] = asset.get("id")
        assets = {k: str(v) for k, v in assets.items() if v}

        summary = self._build_summary(summary_data, candidates)
        logger.info(f"{chemical_system}: {len(candidates)} near-hull candidates")
        return Exploration(chemical_system, candidates, summary, assets)

    def _load_candidates(
        self,
        chemical_system: str,
        run_ids: List[str],
        crystal_systems: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        """Candidates with CIFs: this run's phases first, then known ones.

        The run's own phases are fetched by id, because the general export
        returns only the lowest-energy structures in the whole system and would
        otherwise hide what a constrained exploration actually found.
        """
        exports = []
        if run_ids:
            exports.append(
                {"structure_ids": run_ids, "crystal_systems": None}
            )
        exports.append({"structure_ids": None, "crystal_systems": crystal_systems})

        produced = set(run_ids)
        seen: set = set()
        candidates: List[Dict[str, Any]] = []
        for kwargs in exports:
            try:
                exported = self.ouro.export_candidates(
                    system=chemical_system,
                    max_e_above_hull=self.e_hull_cutoff,
                    dynamically_stable_only=False,
                    **kwargs,
                )
            except Exception as e:
                logger.warning(f"Candidate export failed ({kwargs}): {e}")
                continue

            meta_by_file = {
                m["filename"]: m for m in exported["structures"] if m.get("filename")
            }
            batch = []
            for path, cif_content in exported["cifs"].items():
                filename = path.split("/")[-1]
                meta = meta_by_file.get(filename, {})
                key = meta.get("id") or filename
                if key in seen:
                    continue
                seen.add(key)
                batch.append(self._candidate(filename, cif_content, meta, produced))
            batch.sort(key=lambda c: c["properties"].get("e_hull", float("inf")))
            candidates += batch

        # Listed in two groups, each most stable first: found by this run, then
        # already in the database.
        candidates.sort(key=lambda c: c["origin"] != "this exploration")
        return candidates

    def _candidate(
        self, filename: str, cif_content: str, meta: Dict[str, Any], produced: set
    ) -> Dict[str, Any]:
        formula = meta.get("formula") or self._formula_from_cif_name(filename)
        properties = {
            "e_hull": meta.get("e_above_hull"),
            "dynamic_stability": meta.get("is_dynamically_stable"),
            "num_atoms": meta.get("num_atoms"),
            "space_group": meta.get("space_group_number"),
        }
        properties = {k: v for k, v in properties.items() if v is not None}
        members = {meta.get("id")} | {e.get("id") for e in meta.get("equivalents", [])}
        return {
            "formula": formula or filename,
            "cif": cif_content,
            # GGen names the group when it has matched structures.
            "identity": meta.get("equivalence_group")
            or identity_key(formula or filename, properties),
            "origin": "this exploration"
            if members & produced
            else "already in GGen database",
            "properties": properties,
        }

    def _build_summary(
        self, summary_data: Dict[str, Any], candidates: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        crystal_counts: Counter = Counter(
            crystal_system(c["properties"].get("space_group")) for c in candidates
        )
        on_hull = sum(
            1 for c in candidates if c["properties"].get("e_hull", 1.0) <= 1e-6
        )
        return {
            "structures_generated": summary_data.get("num_successful")
            or summary_data.get("num_candidates"),
            "near_hull": len(candidates),
            "found_by_this_exploration": [
                c["formula"] for c in candidates if c["origin"] == "this exploration"
            ],
            "on_hull": on_hull,
            "crystal_systems": dict(crystal_counts),
            "time_seconds": summary_data.get("total_time_seconds"),
        }

    @staticmethod
    def _formula_from_cif_name(filename: str) -> Optional[str]:
        # e.g. 001_Fe2Co_12meV.cif → Fe2Co
        parts = filename.rsplit(".", 1)[0].split("_")
        return parts[1] if len(parts) >= 2 else None
