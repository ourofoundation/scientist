"""
AI Scientist Agent for Rare-Earth-Free Permanent Magnet Discovery

A modular system for discovering new magnetic materials using AI-guided
hypothesis generation, computational evaluation, and iterative refinement.
"""

__version__ = "0.1.0"
__author__ = "Matt Moderwell"
__email__ = "matt@ouro.foundation"

from .core.scientist import MaterialDiscoveryScientist
from .core.config import ScientistConfig
from .data.models import Material, MaterialProperties, MutationRecord

__all__ = [
    "MaterialDiscoveryScientist",
    "ScientistConfig",
    "Material",
    "MaterialProperties",
    "MutationRecord",
]
