"""Configuration management for the AI Scientist."""

import os
from dataclasses import dataclass, field
from typing import Dict, Any
import dotenv

dotenv.load_dotenv(override=True)

# Prefer production unless explicitly overridden (ouro-py/.env may set localhost).
if not os.getenv("OURO_BACKEND_URL") and not os.getenv("OURO_BASE_URL"):
    os.environ.setdefault("OURO_BACKEND_URL", "https://api.ouro.foundation")


@dataclass
class ScientistConfig:
    """Configuration for the AI Scientist system."""

    # LLM Configuration
    llm_model: str = "openai/gpt-5.2"
    llm_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    llm_max_tokens: int = 32000
    llm_temperature: float = 1.0
    llm_cache: bool = False

    # Discovery Parameters
    max_iterations: int = 5  # chemical systems to explore
    early_stopping_threshold: float = 0.85
    max_candidates_to_evaluate: int = 5  # top survivors per system sent to Ouro

    # Hosted GGen Exploration
    ggen_max_atoms: int = 16
    ggen_min_atoms: int = 2
    ggen_num_trials: int = 10
    ggen_e_hull_cutoff: float = 0.15  # eV/atom
    ggen_max_stoichiometries: int = 100
    ggen_poll_timeout: int = 60 * 60 * 4  # 4h — explorations are long-running

    # Ouro Configuration
    ouro_api_key: str = field(default_factory=lambda: os.getenv("OURO_API_KEY", ""))
    ouro_team_id: str = field(default_factory=lambda: os.getenv("OURO_TEAM_ID", ""))
    ouro_asset_visibility: str = field(
        default_factory=lambda: os.getenv("OURO_ASSET_VISIBILITY", "private")
    )

    # MLflow Configuration
    mlflow_tracking_uri: str = "http://127.0.0.1:5000"
    mlflow_experiment: str = "scientist-optimization"

    # Scoring Weights (normalized in __post_init__)
    scoring_weights: Dict[str, float] = field(
        default_factory=lambda: {
            "e_hull": 0.18,
            "cost": 0.13,
            "magnetic_density": 0.18,
            "magnetic_anisotropy_energy": 0.18,
            "curie_temperature": 0.18,
            "dynamic_stability": 0.13,
            "num_atoms": 0.05,
            "space_group_penalty": 0.05,
        }
    )

    # Default Target Properties
    default_targets: Dict[str, Any] = field(
        default_factory=lambda: {
            "num_atoms_max": 30,
            "cost_max": 100,  # USD / kg
            "magnetic_density_min": 0.10,
            "magnetic_anisotropy_energy_min": 1.5,  # mJ / m^3
            "curie_temperature_min": 500,  # K
            "e_hull_max": 0.150,  # eV / atom
            "dynamic_stability": True,
            "min_space_group": 8,
        }
    )

    def __post_init__(self) -> None:
        total = sum(self.scoring_weights.values())
        if total > 0:
            self.scoring_weights = {
                k: v / total for k, v in self.scoring_weights.items()
            }

    def validate(self) -> None:
        if not self.llm_api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required")
        if not self.ouro_api_key:
            raise ValueError("OURO_API_KEY environment variable is required")
        if not self.ouro_team_id:
            raise ValueError("OURO_TEAM_ID environment variable is required")
        if self.max_iterations <= 0:
            raise ValueError("max_iterations must be positive")
        if not 0 <= self.early_stopping_threshold <= 1:
            raise ValueError("early_stopping_threshold must be between 0 and 1")

        total_weight = sum(self.scoring_weights.values())
        if abs(total_weight - 1.0) > 1e-6:
            raise ValueError(f"Scoring weights must sum to 1.0, got {total_weight}")

    @classmethod
    def from_env(cls) -> "ScientistConfig":
        config = cls()
        config.validate()
        return config
