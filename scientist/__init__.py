"""
AI Scientist Agent for Rare-Earth-Free Permanent Magnet Discovery

A modular system for discovering new magnetic materials using AI-guided
chemical-system proposals, GGen bulk exploration, and Ouro property evaluation.
"""

__version__ = "0.2.0"
__author__ = "Matt Moderwell"
__email__ = "matt@ouro.foundation"

from .core.scientist import MaterialDiscoveryScientist
from .core.config import ScientistConfig
from .data.models import Material, MaterialProperties, ExplorationSummary

__all__ = [
    "MaterialDiscoveryScientist",
    "ScientistConfig",
    "Material",
    "MaterialProperties",
    "ExplorationSummary",
]
