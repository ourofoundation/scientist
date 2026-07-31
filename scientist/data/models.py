"""Data models for the AI Scientist system."""

from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from pymatgen.core.structure import Structure
from uuid import uuid4


@dataclass
class Material:
    """Represents a candidate magnetic material."""

    composition: str  # e.g., "Fe16N2", "MnBi"
    atoms: Structure  # pymatgen structure
    cif_string: str  # CIF string
    file: dict  # Ouro file (or local placeholder until uploaded)
    predicted_properties: Dict
    num_atoms: int
    generation_method: str = "exploration"  # "exploration" | "from_scratch"
    chemical_system: Optional[str] = None  # e.g. "Fe-Co-Bi"
    artifacts: Optional[Dict[str, Dict]] = None
    requested_space_group: Optional[int] = None
    used_space_group: Optional[int] = None
    resolved_space_group: Optional[int] = None
    material_id: str = field(default_factory=lambda: f"mat_{uuid4()}")

    def to_json(self) -> Dict[str, Any]:
        """Convert material to dictionary."""
        return {
            "material_id": self.material_id,
            "composition": self.composition,
            "space_group": self.resolved_space_group,
            "generation_method": self.generation_method,
            "chemical_system": self.chemical_system,
            "predicted_properties": self.predicted_properties,
            "cif_string": self.cif_string,
        }


@dataclass
class MaterialProperties:
    """Key properties for bulk crystalline magnetic materials.

    All properties are Optional to support partial evaluation results
    when some property prediction routes fail.
    """

    curie_temperature: Optional[float] = None  # Tc in K
    magnetic_density: Optional[float] = None
    magnetic_anisotropy_energy: Optional[float] = (
        None  # Magnetic anisotropy energy in mJ / m^3
    )
    cost: Optional[float] = None  # Cost in USD / kg
    e_hull: Optional[float] = None  # Energy above hull in eV / atom
    dynamic_stability: Optional[bool] = None  # True or False
    space_group: Optional[int] = None  # Space group number
    num_atoms: Optional[int] = None  # Number of atoms
    evaluation_errors: Dict[str, str] = field(default_factory=dict)

    def has_minimum_properties(self) -> bool:
        """Check if we have enough properties to be useful for scoring."""
        key_props = [
            self.curie_temperature,
            self.magnetic_density,
            self.magnetic_anisotropy_energy,
            self.e_hull,
        ]
        return sum(p is not None for p in key_props) >= 2

    @property
    def failed_evaluations(self) -> List[str]:
        """Return list of properties that failed to evaluate."""
        return list(self.evaluation_errors.keys())


@dataclass
class ExplorationSummary:
    """Compact summary of a GGen chemical-system exploration for the LLM."""

    chemical_system: str
    num_candidates: int
    num_successful: int
    num_on_hull: int
    num_near_hull: int
    num_evaluated: int
    crystal_system_counts: Dict[str, int]
    best_candidates: List[Dict[str, Any]]  # formula, e_hull, sg, score if known
    time_seconds: float = 0.0
    hypothesis: str = ""
    insights: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chemical_system": self.chemical_system,
            "num_candidates": self.num_candidates,
            "num_successful": self.num_successful,
            "num_on_hull": self.num_on_hull,
            "num_near_hull": self.num_near_hull,
            "num_evaluated": self.num_evaluated,
            "crystal_system_counts": self.crystal_system_counts,
            "best_candidates": self.best_candidates,
            "time_seconds": self.time_seconds,
            "hypothesis": self.hypothesis,
            "insights": self.insights,
        }
