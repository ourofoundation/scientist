# CLAUDE.md

Guidance for working in this repository.

## Project Overview

An AI scientist for materials research on Ouro. A plain-language goal is
compiled into a measurable objective, and an annealing search pursues it: the
harness judges where the goal stands, the LLM proposes moves, the harness
selects one by temperature, and every step is remembered across runs. See `README.md` for the full loop.

## Design rules

- **The harness owns control flow.** Budget, temperature, move selection and
  the verdict on the goal (`Search.judge`: met / not yet / blocked, computed
  from measurements, never by a model) are code in `core/search.py`. The LLM is called for bounded
  decisions with a schema (propose, triage, reflect). Do not turn this into a
  free-running tool loop — that is what ouro-agents is for. Where a model does
  need to run free (research), it goes through `core/agent.py`: read-only
  tools, a turn and cost budget, and a typed result.
- **A person steers through directives, never by talking to the model.**
  `scientist/watch.py` writes rows to `directives`; the harness reads them at
  step boundaries (`_controls`, `_queued_move`, `_await_choice`, steer notes)
  and records them on the node. The run waits only while a `pause` is held.
  The harness reports what it is doing through `Search._emit` (the `events`
  table), which must never be able to fail a run.
- **A calculation error is "unknown", not a negative result.** Keep that
  distinction visible in every prompt that shows measurements.
- **Nothing outside `computational/catalog.py` knows which route measures
  what.** No property names or magnet-specific logic in the search, objective,
  or report code. New capabilities are catalog entries.
- **Memory is private.** The search tree lives in SQLite (`core/store.py`),
  on a Modal Volume in production. Only the run report and the assets a run
  produces go to Ouro.
- **The report may only cite real assets.** `utils/report.py` builds an
  inventory of ids and strips any embed or link not in it.

## Core Components

- `scientist/core/search.py` — the loop and the LLM decision schemas/prompts
- `scientist/core/objective.py` — `ObjectiveSpec`, goal compilation, scoring
- `scientist/core/store.py` — campaigns, nodes, candidates, learnings
- `scientist/core/agent.py`, `tools.py` — bounded sub-agent pattern and its tools
- `scientist/core/llm.py` — OpenRouter client, prompt caching, call records
- `scientist/computational/` — catalog, GGen explorer, evaluator, Ouro client,
  and `sim.py`, the simulated world behind `--sim`
- `scientist/watch.py`, `watch.html` — `scientist --watch`: live view and direction
- `scientist/utils/report.py` — LLM-written report and publishing

## Essential Commands

```bash
pip install -e .
scientist "<goal>" --steps 2
scientist --sim --steps 10 --oracle   # simulated world: ~1 min, a few cents
scientist --watch --sim               # live view of the simulated memory
pytest tests
```

`--sim` swaps GGen and the property routes for `computational/sim.py` (a
seeded synthetic world with a known best answer), uses a cheap model
(`SIM_MODEL` in `core/config.py`, or `--model`), keeps memory in
`~/.scientist/sim-<seed>.db`, and sends nothing to Ouro. Use it for any change
to the loop's logic; `--oracle` prints how close the run got to the best
possible score.

## Environment

- `OPENROUTER_API_KEY`
- `OURO_API_KEY`
- `OURO_TEAM_ID`

## Dependencies

- openai (pointed at OpenRouter), pydantic, ouro-py, pandas, requests
- Hosted GGen on Ouro (no local ggen package)
