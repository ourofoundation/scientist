"""Catalog of measurable properties and the Ouro routes that produce them.

The catalog is what makes the scientist task-agnostic: a research goal is
compiled into criteria over these properties, and nothing outside this file
knows which route measures what. Add a capability by adding an entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

# Tiers: "free" comes with the GGen exploration (route is only a fallback),
# "cheap" routes run in seconds–minutes, "expensive" ones are multi-hour DFT.
FREE, CHEAP, EXPENSIVE = "free", "cheap", "expensive"


@dataclass(frozen=True)
class PropertySpec:
    name: str
    description: str
    unit: str
    tier: str
    route: Optional[str] = None
    extract: Optional[Callable[[Dict[str, Any]], Any]] = None
    body: Optional[Dict[str, Any]] = None
    boolean: bool = False
    input_name: str = "file"  # the route's name for its structure input


CATALOG: Dict[str, PropertySpec] = {
    p.name: p
    for p in [
        PropertySpec(
            name="e_hull",
            description="Energy above the convex hull (thermodynamic stability)",
            unit="eV/atom",
            tier=FREE,
            route="mmoderwell/calculate-energy-above-the-convex-hull",
            extract=lambda r: r["e_above_hull"],
            # GGen relaxes with a different potential, so let the route re-relax.
            body={"allow_unrelaxed": True},
            input_name="structure",
        ),
        PropertySpec(
            name="dynamic_stability",
            description="True when the phonon spectrum has no imaginary modes",
            unit="boolean",
            tier=FREE,
            route="mmoderwell/check-phonon-stability",
            extract=lambda r: not bool(r["imaginary_modes_detected"]),
            boolean=True,
        ),
        PropertySpec(
            name="num_atoms",
            description="Atoms in the unit cell (smaller is simpler to make)",
            unit="atoms",
            tier=FREE,
        ),
        PropertySpec(
            name="space_group",
            description="Space group number (1-230); higher means more symmetric",
            unit="number",
            tier=FREE,
        ),
        PropertySpec(
            name="cost",
            description="Raw-material cost from elemental prices",
            unit="USD/kg",
            tier=CHEAP,
            route="hermes/estimate-raw-material-cost-per-kg",
            extract=lambda r: r["cost_per_kg"]["value"],
        ),
        PropertySpec(
            name="curie_temperature",
            description="Predicted Curie temperature (ML model)",
            unit="K",
            tier=CHEAP,
            route="hermes/predict-curie-temperature-from-a-cif",
            extract=lambda r: r["temperature"],
        ),
        PropertySpec(
            name="saturation_magnetization",
            description="Saturation magnetization from predicted magnetic moments",
            unit="T",
            tier=CHEAP,
            route="hermes/estimate-magnetic-moments-and-ms-from-a-cif",
            extract=lambda r: r["saturation_magnetization"]["tesla"]["value"],
        ),
        PropertySpec(
            name="magnetic_anisotropy_energy",
            description=(
                "Magnetocrystalline anisotropy energy from DFT (positive = "
                "uniaxial easy axis). Multi-hour calculation."
            ),
            unit="MJ/m^3",
            tier=EXPENSIVE,
            route="mmoderwell/calculate-magnetic-anisotropy-energy-mae",
            extract=lambda r: r["mae_mj_per_m3"],
            body={
                "method": "tb2j",
                "ecutwfc": 65,
                "scf_thr": 0.000001,
                "kspacing": 0.16,
                "scf_nmax": 200,
                "smearing_sigma_ev": 0.05,
                "smearing_method": "mp",
            },
        ),
    ]
}


def describe_catalog(catalog: Optional[Dict[str, PropertySpec]] = None) -> str:
    """Catalog as prompt text."""
    catalog = catalog or CATALOG
    return "\n".join(
        f"- {p.name} [{p.unit}, {p.tier}]: {p.description}" for p in catalog.values()
    )
