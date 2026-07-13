"""Configuration management for the AI Scientist."""

import os
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import dotenv

dotenv.load_dotenv(override=True)


@dataclass
class ScientistConfig:
    """Configuration for the AI Scientist system."""

    # LLM Configuration
    llm_model: str = "openai/gpt-5.2"  # gpt-4.1-mini for gpt-5
    llm_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    llm_max_tokens: int = 32000
    llm_temperature: float = 1.0  # must be 1.0 for gpt-5.0
    llm_cache: bool = False

    # Discovery Parameters
    max_iterations: int = 20
    early_stopping_threshold: float = 0.9

    # Ouro Configuration
    ouro_api_key: str = field(default_factory=lambda: os.getenv("OURO_API_KEY", ""))
    ouro_team_id: str = field(default_factory=lambda: os.getenv("OURO_TEAM_ID", ""))
    ouro_asset_visibility: str = field(
        default_factory=lambda: os.getenv("OURO_ASSET_VISIBILITY", "private")
    )

    # MLflow Configuration
    mlflow_tracking_uri: str = "http://127.0.0.1:5000"
    mlflow_experiment: str = "scientist-optimization"

    # Scoring Weights
    scoring_weights: Dict[str, float] = field(
        default_factory=lambda: {
            "e_hull": 0.18,
            "cost": 0.13,
            "magnetic_density": 0.18,
            "magnetic_anisotropy_energy": 0.18,
            "curie_temperature": 0.18,
            "dynamic_stability": 0.13,
            "num_atoms": 0.1,
            "space_group_penalty": 0.1,
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
            "min_space_group": 8,  # Minimum acceptable space group number
        }
    )

    def __post_init__(self) -> None:
        """Normalize scoring weights to sum to 1.0."""
        total = sum(self.scoring_weights.values())
        if total > 0:
            self.scoring_weights = {
                k: v / total for k, v in self.scoring_weights.items()
            }

    def validate(self) -> None:
        """Validate configuration values."""

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

        # Validate scoring weights sum to 1.0
        total_weight = sum(self.scoring_weights.values())
        if abs(total_weight - 1.0) > 1e-6:
            raise ValueError(f"Scoring weights must sum to 1.0, got {total_weight}")

    @classmethod
    def from_env(cls) -> "ScientistConfig":
        """Create configuration from environment variables."""
        config = cls()
        config.validate()
        return config
