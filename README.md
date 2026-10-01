# Scientist

An AI scientist for materials research on Ouro. Give it a goal in plain
language; it searches chemical space for materials that meet it, remembers what
it tried, and writes up each run.

```bash
scientist "Find a rare-earth-free permanent magnet with high MAE"
scientist "Find a Gd-based permanent magnet with a Curie temperature above 600 K"
```

## How it works

The search is simulated annealing with an LLM as the proposal distribution.
The harness owns the loop; the LLM supplies direction.

1. **Compile the goal.** The goal becomes an objective: criteria over
   measurable properties (targets, weights, hard constraints) plus required
   and forbidden elements. Compiled once per campaign.
2. **Judge.** Before every step the harness decides, from the measurements
   alone, where the goal stands: *met* (a candidate has every criterion
   measured and meets every target), *blocked* (the expensive-calculation
   allowance is spent and enough candidates already wait for one), or *not
   yet*, with what is missing. Met and blocked end the run; the gap is shown
   to the LLM before it proposes.
3. **Propose.** From the best position so far, the path that led there, every
   measured candidate, and the learnings so far, the LLM proposes several moves,
   each with a predicted score. A move explores a chemical system with GGen,
   deepens a system already explored, or spends calculations on candidates
   already found. Before proposing, a bounded research sub-agent searches the
   literature and queries the search memory, and returns a sourced brief.
4. **Select.** The harness picks a move with probability ∝ exp(predicted / T).
   The temperature falls from `t_initial` to `t_min` over the run's steps and
   reheats when the search stalls. A move that repeats an earlier exploration
   without widening it is refused.
5. **Execute.** GGen explores and the LLM triages which near-hull candidates
   get the cheap property routes. Expensive properties are spent only by
   `evaluate` moves, and only on candidates that could still beat the best
   fully measured one.
6. **Reflect.** The LLM compares prediction with result and records learnings.
   The search continues from the best node so far.

A score counts only measured criteria. A partly measured candidate also has a
ceiling — its score if everything unmeasured came out perfectly — which is what
decides whether an expensive calculation on it is worth running.

Every step is a node in a tree stored in a private SQLite database, so a later
run of the same goal resumes the same campaign from where it stopped.

At the end of a run the LLM writes the report as an Ouro post, embedding the
structures, phase diagrams, route runs and a candidates dataset the run produced.

## Sub-agents

`core/agent.py` is a general pattern, not specific to research: give
`run_agent` a task, a list of `Tool`s, a result schema, and a turn and cost
budget. The model works with the tools and ends by calling `finish` with a
result that is validated against the schema; if the budget runs out first, the
result is recovered from the conversation. The caller always gets a typed
object back. The research step is one use (`ResearchBrief`).

## Costs and caching

Every LLM call is recorded with its purpose, tokens (including cached and
cache-write), dollar cost as reported by OpenRouter, and latency. Every Ouro
route run is recorded with its charge and compute time. Both are stored per
run and per step in the memory database (`llm_calls`, `route_calls`) and
summarised in the log and the report footer.

The search record is sent as a shared, cached prefix, so the research and
proposal calls within a step re-read it at the cached price, and a sub-agent's
growing conversation is cached turn to turn.

## What it can pursue

A goal is feasible when its properties are in the catalog
(`scientist/computational/catalog.py`): energy above hull, dynamic stability,
raw-material cost, Curie temperature, saturation magnetization, magnetic
anisotropy energy, cell size, space group. Anything in a goal the catalog
cannot measure is reported as unsupported when the objective is compiled.
Add a capability by adding a catalog entry that names the Ouro route and how
to read its response.

## Package layout

```
scientist/
  core/
    search.py      # the annealing loop: judge, propose, select, execute, reflect
    objective.py   # goal → ObjectiveSpec, scoring
    store.py       # SQLite memory: campaigns, nodes, candidates, learnings
    agent.py       # bounded sub-agent: tool loop, budget, typed result
    tools.py       # read-only tools: web search, page reader, memory SQL
    llm.py         # OpenRouter client, prompt caching, per-call cost records
    config.py
  computational/
    catalog.py     # measurable properties → Ouro routes
    sim.py         # simulated world for testing the loop (`--sim`)
    explorer.py    # hosted GGen exploration
    evaluator.py   # runs catalog routes on a candidate
    ouro_client.py # Ouro + GGen route client
  utils/report.py  # LLM-written report, asset inventory, publishing
  main.py          # CLI
modal_app.py       # daily scheduled run; memory on a Modal Volume
tests/             # search loop against fake backends
```

## Running

```bash
pip install -e .

scientist "<goal>"                 # new campaign, or resume the same goal
scientist "<goal>" --steps 3       # shorter run
scientist --campaign <key>         # resume a campaign by key
scientist --list                   # campaigns in memory

# Simulated world: instant calculations, cheap model, separate memory,
# nothing sent to Ouro. --oracle prints the best score the world contains.
scientist --sim --steps 12 --oracle [--seed N] [--model <openrouter id>]

pytest tests
```

## Environment

Required: `OPENROUTER_API_KEY`, `OURO_API_KEY`, `OURO_TEAM_ID`.

| Variable | Default | Meaning |
|----------|---------|---------|
| `SCIENTIST_MODEL` | `anthropic/claude-opus-5.5` | OpenRouter model |
| `SCIENTIST_REASONING_EFFORT` | `high` (`low` with `--sim`) | Reasoning effort sent to the model |
| `SCIENTIST_DB` | `~/.scientist/scientist.db` | Memory database |
| `SCIENTIST_RESEARCH` | `true` | Research sub-agent before each step |
| `SCIENTIST_WEB_RESEARCH` | `true` | Give that sub-agent web tools (else memory only) |
| `SCIENTIST_SEARCH_MODEL` | `anthropic/claude-haiku-4.5` | Model behind the `web_search` tool |
| `SCIENTIST_GOAL` | RE-free magnet goal | Goal for the scheduled Modal run |
| `OURO_ASSET_VISIBILITY` | `private` | Visibility of posts and assets |

## Config knobs (`ScientistConfig`)

| Setting | Default | Meaning |
|---------|---------|---------|
| `max_steps` | 5 | Search steps per run (the run's budget; also sets the cooling schedule) |
| `max_expensive_evals` | 3 | Multi-hour DFT calculations per run |
| `max_cheap_evals_per_step` | 6 | Candidates sent to cheap routes per step |
| `proposals_per_step` | 4 | Moves the LLM proposes per step |
| `research_max_turns` / `research_max_cost_usd` | 8 / 3.0 | Research sub-agent budget per step |
| `ggen_max_atoms_cap` / `ggen_num_trials_cap` | 32 / 20 | Ceilings on what a move may request |
| `t_initial` / `t_min` | 0.25 / 0.02 | Temperature at the first and last step (score scale 0–1) |
| `stall_steps` | 3 | Productive steps without a new best before reheating |
| `ggen_e_hull_cutoff` | 0.15 | eV/atom near-hull cutoff |
| `ggen_poll_timeout` | 14400 | Seconds to wait on a hosted exploration |
