"""Data models for the AI Scientist system."""

from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from pymatgen.core.structure import Structure
from datetime import datetime
from uuid import uuid4


@dataclass
class MutationRecord:
    """Records a single mutation operation and its effects."""

    mutation_type: str  # e.g., "scale_lattice", "substitute", "jitter_sites"
    parameters: Dict[str, Any]  # mutation parameters
    timestamp: datetime
    parent_composition: str
    child_composition: str
    property_changes: Dict[str, float] = field(
        default_factory=dict
    )  # change in properties
    success: bool = True
    error_message: Optional[str] = None


@dataclass
class Material:
    """Represents a candidate magnetic material."""

    composition: str  # e.g., "Fe16N2", "MnBi"
    atoms: Structure  # pymatgen structure
    cif_string: str  # CIF string
    file: dict  # Ouro file
    predicted_properties: Dict
    num_atoms: int
    # Generation method tracking
    generation_method: str = "from_scratch"  # "from_scratch", "mutation"
    parent_material_id: Optional[str] = None  # ID of parent if mutation
    mutation_history: List[MutationRecord] = field(default_factory=list)
    # Ouro route call responses
    artifacts: Optional[Dict[str, Dict]] = None
    # Space group tracking
    requested_space_group: Optional[int] = None  # proposed by the agent
    used_space_group: Optional[int] = (
        None  # passed to generator after compatibility check
    )
    resolved_space_group: Optional[int] = None  # determined from resulting structure
    # Unique identifier for tracking lineage
    material_id: str = field(default_factory=lambda: f"mat_{uuid4()}")

    def to_json(self) -> Dict[str, Any]:
        """Convert material to dictionary."""
        return {
            "material_id": self.material_id,
            "composition": self.composition,
            "space_group": self.resolved_space_group,
            "generation_method": self.generation_method,
            "parent_material_id": self.parent_material_id,
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
    e_hull: Optional[float] = None  # Energy of hull in eV / atom
    dynamic_stability: Optional[bool] = None  # True or False
    space_group: Optional[int] = None  # Space group number
    num_atoms: Optional[int] = None  # Number of atoms
    # Track evaluation errors for transparency
    evaluation_errors: Dict[str, str] = field(default_factory=dict)

    def has_minimum_properties(self) -> bool:
        """Check if we have enough properties to be useful for scoring."""
        # Consider valid if we have at least 2 key properties
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
