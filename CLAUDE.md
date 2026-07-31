# CLAUDE.md

Guidance for working in this repository.

## Project Overview

AI-powered materials discovery for rare-earth-free permanent magnets. An LLM
proposes chemical systems; Ouro-hosted GGen explores them in bulk; Ouro routes
evaluate magnetic/cost properties on the survivors.

## Key Architecture

1. **System Proposal** (`ProposeChemicalSystem`): LLM proposes a chemical system
   (e.g. `Fe-Co-Bi`) plus optional crystal-system and composition constraints.
2. **Hosted Exploration** (`SystemExplorer` → Ouro GGen explore route): enumerate
   stoichiometries, generate/relax structures, return near-hull CIFs + summary.
3. **Property Evaluation** (`MaterialEvaluator`): Ouro routes for Curie temp,
   magnetization density, cost, and conditional MAE. Prefer exploration-provided
   `e_hull` when available.
4. **Interpretation** (`InterpretExplorationResults`): LLM summarizes and guides
   the next system.

No local GGen install is required. Mutation / evolutionary structure ops are out
of scope.

### Core Components

- `scientist/core/scientist.py` — discovery loop
- `scientist/computational/explorer.py` — hosted GGen exploration
- `scientist/computational/ouro_client.py` — Ouro + GGen route client
- `scientist/computational/evaluator.py` — two-tier evaluation
- `scientist/computational/scorer.py` — soft-saturation scoring

## Essential Commands

```bash
pip install -e .
python -m scientist.main
```

## Environment

- `OPENAI_API_KEY`
- `OURO_API_KEY`
- `OURO_TEAM_ID`

## Dependencies

- DSPy, PyMatGen, MLflow, ouro-py, OpenAI, requests
- Hosted GGen on Ouro (no local ggen package)
