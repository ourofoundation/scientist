# Scientist

AI-powered rare-earth-free permanent magnet discovery. An LLM proposes
**chemical systems**; Ouro-hosted GGen explores them in bulk (stoichiometries →
MLIP relaxation → convex hull); near-hull survivors are evaluated on Ouro for
magnetic and cost properties.

## Architecture

1. **Landscape analysis** (`AnalyzeMagnetLandscape`) — identify promising directions
2. **System proposal** (`ProposeChemicalSystem`) — LLM picks a chemical system +
   crystal-system / composition constraints (not a single formula)
3. **Hosted GGen exploration** (`SystemExplorer` →
   `mmoderwell/explore-a-chemical-system-with-ggen`) — enumerate stoichiometries,
   generate/relax structures, build the phase diagram, return near-hull CIFs
4. **Ouro evaluation** (`MaterialEvaluator`) — Curie temperature, magnetization
   density, cost, and (conditionally) MAE. Local GGen `e_hull` from the
   exploration summary is preferred over re-computing remotely.
5. **Interpretation** (`InterpretExplorationResults`) — LLM reads the exploration
   summary + scored candidates and guides the next system proposal

No local GGen / GPU stack is required — exploration and generation run on Ouro.

## Package layout

```
scientist/
  agents/signatures.py     # DSPy signatures
  computational/
    explorer.py            # hosted GGen exploration wrapper
    evaluator.py           # two-tier Ouro property evaluation
    scorer.py              # soft-saturation scoring
    structure_generator.py # hosted single-structure fallback
    ouro_client.py         # Ouro + GGen route client
    tools.py               # facade
  core/
    scientist.py           # discovery loop
    config.py
  data/models.py
  utils/publisher.py
  main.py
```

## Running

```bash
pip install -e .

# MLflow (optional but configured by default)
mlflow server --backend-store-uri sqlite:///scientist.sqlite

python -m scientist.main
# or
scientist
```

## Environment

Required:

- `OPENAI_API_KEY`
- `OURO_API_KEY`
- `OURO_TEAM_ID`

## Config knobs

| Setting | Default | Meaning |
|---------|---------|---------|
| `max_iterations` | 5 | Chemical systems to explore |
| `max_candidates_to_evaluate` | 5 | Near-hull survivors sent to magnetic eval |
| `ggen_max_atoms` | 16 | Max atoms per formula unit |
| `ggen_num_trials` | 10 | Generation attempts per stoichiometry |
| `ggen_e_hull_cutoff` | 0.15 | eV/atom near-hull cutoff |
| `ggen_poll_timeout` | 14400 | Seconds to wait on hosted explore (4h) |
| `early_stopping_threshold` | 0.85 | Stop when best score exceeds this |

## Hosted GGen routes used

- Explore: `mmoderwell/explore-a-chemical-system-with-ggen`
- Export: `mmoderwell/export-candidate-cifs` (fallback for CIFs)
- Generate: `mmoderwell/generate-a-crystal-structure-using-ggen` (single-structure fallback)
