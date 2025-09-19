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
    """Key properties for bulk crystalline magnetic materials."""

    curie_temperature: float  # Tc in K
    magnetic_density: float
    cost: float  # Cost in USD / kg
    e_hull: float  # Energy of hull in eV / atom
    dynamic_stability: bool  # True or False
    space_group: int  # Space group number
    num_atoms: int  # Number of atoms
