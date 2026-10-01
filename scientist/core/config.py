"""Configuration management for the AI Scientist."""

import os
from dataclasses import dataclass, field
from typing import Optional

import dotenv

dotenv.load_dotenv(override=True)

# Prefer production unless explicitly overridden (ouro-py/.env may set localhost).
if not os.getenv("OURO_BACKEND_URL") and not os.getenv("OURO_BASE_URL"):
    os.environ.setdefault("OURO_BACKEND_URL", "https://api.ouro.foundation")

DEFAULT_GOAL = (
    "Find a rare-earth-free permanent magnet with high magnetic anisotropy "
    "energy, a Curie temperature above 500 K, and low raw-material cost."
)
# Default model for `--sim` runs, where the loop is under test, not the chemistry.
SIM_MODEL = "google/gemini-3.5-flash-lite"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class ScientistConfig:
    """Configuration for the AI Scientist system."""

    # LLM (OpenRouter)
    llm_model: str = field(
        default_factory=lambda: os.getenv(
            "SCIENTIST_MODEL", "anthropic/claude-opus-5.5"
        )
    )
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = field(
        default_factory=lambda: os.getenv("OPENROUTER_API_KEY", "")
    )
    llm_max_tokens: int = 32000
    llm_reasoning_effort: str = field(
        default_factory=lambda: os.getenv("SCIENTIST_REASONING_EFFORT", "high")
    )
    # Research sub-agent that runs before each proposal. `web_research` gates
    # its web tools; without them it can still query the search memory.
    research: bool = field(default_factory=lambda: _env_bool("SCIENTIST_RESEARCH", True))
    web_research: bool = field(
        default_factory=lambda: _env_bool("SCIENTIST_WEB_RESEARCH", True)
    )
    research_max_turns: int = 8
    research_max_cost_usd: float = 3.0
    # Model behind the web_search tool: it only summarises search results.
    search_model: Optional[str] = field(
        default_factory=lambda: os.getenv(
            "SCIENTIST_SEARCH_MODEL", "anthropic/claude-haiku-4.5"
        )
    )

    # Private memory (SQLite). On Modal this lives on a Volume.
    db_path: str = field(
        default_factory=lambda: os.getenv(
            "SCIENTIST_DB", os.path.expanduser("~/.scientist/scientist.db")
        )
    )

    # Per-run budget
    max_steps: int = 5
    max_runtime_seconds: int = 60 * 60 * 20
    max_expensive_evals: int = 3  # per run
    max_expensive_evals_per_step: int = 2
    max_cheap_evals_per_step: int = 6
    proposals_per_step: int = 4

    # Annealing. Scores live in [0, 1], so temperatures are on that scale.
    # The temperature falls from t_initial to t_min over the run's steps.
    t_initial: float = 0.25
    t_min: float = 0.02
    stall_steps: int = 3  # productive steps without a new best before reheating

    # Hosted GGen exploration defaults (a move may override max_atoms/num_trials)
    ggen_max_atoms: int = 16
    ggen_min_atoms: int = 2
    ggen_num_trials: int = 10
    ggen_e_hull_cutoff: float = 0.15  # eV/atom
    ggen_max_stoichiometries: int = 100
    ggen_max_atoms_cap: int = 32  # ceilings on what a move may ask for
    ggen_num_trials_cap: int = 20
    ggen_poll_timeout: int = 60 * 60 * 4  # 4h — explorations are long-running
    max_candidates_stored: int = 60  # near-hull candidates kept per exploration

    # Ouro
    ouro_api_key: str = field(default_factory=lambda: os.getenv("OURO_API_KEY", ""))
    ouro_team_id: str = field(default_factory=lambda: os.getenv("OURO_TEAM_ID", ""))
    ouro_asset_visibility: str = field(
        default_factory=lambda: os.getenv("OURO_ASSET_VISIBILITY", "private")
    )

    # Simulated world (`--sim`): GGen and the property routes are replaced by
    # computational/sim.py, nothing is sent to Ouro, and memory is kept apart.
    sim: bool = False
    sim_seed: int = 0
    sim_failure_rate: float = field(
        default_factory=lambda: float(os.getenv("SCIENTIST_SIM_FAILURE_RATE", "0"))
    )
    # Shown to the model with the search record; says what world it is in.
    world_note: str = ""

    def use_sim(self, seed: int = 0) -> None:
        """Switch to the simulated world, with settings that keep it cheap."""
        from ..computational.sim import WORLD_NOTE

        self.sim, self.sim_seed, self.world_note = True, seed, WORLD_NOTE
        # Real literature says nothing about an invented world.
        self.web_research = False
        self.research = _env_bool("SCIENTIST_RESEARCH", False)
        if not os.getenv("SCIENTIST_MODEL"):
            self.llm_model = SIM_MODEL
        if not os.getenv("SCIENTIST_REASONING_EFFORT"):
            self.llm_reasoning_effort = "low"
        # Simulated learnings must never reach the real memory.
        if not os.getenv("SCIENTIST_DB"):
            self.db_path = os.path.expanduser(f"~/.scientist/sim-{seed}.db")

    def validate(self) -> None:
        if not self.llm_api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable is required")
        if not self.sim and not self.ouro_api_key:
            raise ValueError("OURO_API_KEY environment variable is required")
        if not self.sim and not self.ouro_team_id:
            raise ValueError("OURO_TEAM_ID environment variable is required")
        if self.max_steps <= 0:
            raise ValueError("max_steps must be positive")
        if not 0 < self.t_min <= self.t_initial:
            raise ValueError("need 0 < t_min <= t_initial")

    @classmethod
    def from_env(cls, sim: bool = False, sim_seed: int = 0) -> "ScientistConfig":
        config = cls()
        if sim:
            config.use_sim(sim_seed)
        config.validate()
        return config
