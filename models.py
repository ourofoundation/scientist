# ============================================================================
# Data Structures
# ============================================================================

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from pymatgen.core.structure import Structure


@dataclass
class Material:
    """Represents a candidate magnetic material"""

    composition: str  # e.g., "Fe16N2", "MnBi"
    atoms: Structure  # pymatgen structure
    cif_string: str  # CIF string
    file: dict  # Ouro file
    predicted_properties: Dict
    num_atoms: int
    # Ouro route call responses
    artifacts: Optional[Dict[str, Dict]] = None
    # Space group tracking
    requested_space_group: Optional[int] = None  # proposed by the agent
    used_space_group: Optional[int] = (
        None  # passed to generator after compatibility check
    )
    resolved_space_group: Optional[int] = None  # determined from resulting structure


@dataclass
class MaterialProperties:
    """Key properties for bulk crystalline magnetic materials"""

    # saturation_magnetization: float  # Ms in T
    # coercivity: float  # Hc in kA/m
    # energy_product: float  # BHmax in kJ/m³
    # magnetocrystalline_anisotropy: float  # K1 in MJ/m³
    curie_temperature: float  # Tc in K
    magnetic_density: float
    cost: float  # Cost in USD / kg
    e_hull: float  # Energy of hull in eV / atom
    dynamic_stability: bool  # True or False
