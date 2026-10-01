"""
AI Scientist: an LLM-guided annealing search over materials.

A research goal is compiled into a measurable objective; the search walks
chemical space with the LLM proposing moves, a harness choosing and accepting
them by temperature, and every step remembered across runs.
"""

__version__ = "0.3.0"
__author__ = "Matt Moderwell"
__email__ = "matt@ouro.foundation"

from .core.config import ScientistConfig

__all__ = ["ScientistConfig"]
