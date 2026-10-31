# Lab: the scientist as a Claude Code skill

The `scientist` package runs research as a fixed loop of bounded LLM calls
(see the top-level README). This folder tries a different shape: Claude Code
itself is the scientist. It has tools for the literature (Firecrawl MCP) and
for experiments (Ouro MCP routes). A skill sets out the scientific method it
follows, and a private notebook carries what it learned from one session to
the next.

```
lab/
  skill/
    SKILL.md          the research loop the agent follows
    notebook.md       how experiments, hypotheses and learnings are recorded
    routes.md         the instruments: which route measures what, tiers, habits
    research.md       literature, novelty checks, staying novel, backtesting
    scripts/notebook.py   init / new campaign / open runs / calibration / spend
  notebook/           private, gitignored here; its own local git repo
```

## What carries over from the harness

- Predictions are written before results, and checked against them
  (`experiments.jsonl`, `notebook.py calibration`).
- Cheap measurements first. Expensive runs only when a candidate could still
  beat the best fully measured one.
- The goal is judged from measurements only. A failed calculation is
  unknown, never bad.
- Memory is private. Reports cite only real asset ids.

## What changes

- The agent designs experiments, not just picks chemical systems. It can
  build structures (substitutions, defects, strain) with pymatgen or the
  build routes, and test a mechanism directly.
- Hypotheses are first-class, with verdicts, and one slot is always held for
  a claim with no precedent in the literature.
- Research reads papers in full (Firecrawl), and every candidate gets a
  novelty check: known, computed-only or new.
- Long DFT runs don't block anything. The notebook records what's in flight,
  and the next session picks it up.

## Use

Claude Code finds the skill through `.claude/skills/scientist`, a link to
`lab/skill`. Start a session in this repo (or the workspace) with the
Ouro and Firecrawl connectors on, and give it a goal:

> Find a rare-earth-free permanent magnet with K1 above 1 MJ/m³ and Js above 1 T.

> Continue the re-free-magnet campaign.

The first session on a new goal ends after framing: the agent proposes the
objective and budget and waits for your OK before spending.
