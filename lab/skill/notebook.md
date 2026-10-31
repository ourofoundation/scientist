# The lab notebook

The notebook is how the scientist remembers. A session starts with no memory
except what is written here, so anything not written down did not happen.
Write as you go, not at the end: a session can stop at any point.

The notebook lives at `lab/notebook/` (override with `SCIENTIST_NOTEBOOK`).
It is ignored by the scientist repo, which is public, and is its own private
git repository, so `git log` in it is the history of the research.

```
notebook/
  index.md                    every campaign, one row each
  learnings.md                lessons that hold beyond one campaign
  campaigns/
    <slug>/
      campaign.md             goal, objective, budget, status, best so far
      hypotheses.md           claims under test and their verdicts
      candidates.md           every structure considered, with novelty and scores
      experiments.jsonl       one line per experiment, appended, never rewritten
      log.md                  one dated entry per session
```

Ouro holds the evidence: structures, route runs, outputs, posts. The notebook
holds the reasoning and points at the evidence by id. Never paste numbers into
the notebook without the action or asset id they came from.

## index.md

```markdown
| Campaign | Goal | Status | Best so far | Last session |
|---|---|---|---|---|
| [re-free-magnet](campaigns/re-free-magnet/campaign.md) | RE-free permanent magnet, K1 > 1 MJ/m³ uniaxial, Js > 1 T | active | Fe6SiP2: K1 1.83, Js 1.19 (unconverged) | 2026-10-31 |
```

Status is one of `active`, `waiting` (only long runs left in flight),
`met`, `blocked`, `parked`.

## campaign.md

```markdown
# <Campaign title>

Goal (the user's words): ...

## Objective
| Criterion | Property | Target | Weight | Route | Tier |
|---|---|---|---|---|---|
| Stable | e_hull | ≤ 0.05 eV/atom | hard | mmoderwell/calculate-energy-above-the-convex-hull | cheap |
| Strong anisotropy | K1 | ≥ 1.0 MJ/m³, easy axis | 0.4 | mmoderwell/magnetic-anisotropy-energy | expensive |

Not measurable with our routes: ... (say so plainly; never score it from literature)

Constraints: required / forbidden elements, max atoms, anything else.

## Budget
- Spend cap: $<n> (spent so far: $<m>, from experiments.jsonl)
- Expensive runs: <k> allowed, <j> used
- Where runs go: org <name/id>, team <name/id>, visibility <private/...>

## Status
<one paragraph: where the goal stands, judged only from measurements —
met / not yet (what is missing) / blocked (why)>

## Best so far
<candidate id, formula, asset id, the measured values, what is still unmeasured>
```

Update Status and Best so far at the end of every session.

## hypotheses.md

A hypothesis is a claim that an experiment can prove wrong. Write the
prediction before the test runs; never edit it afterwards.

```markdown
## H7: Si on the P site of Fe2P keeps uniaxial anisotropy [open]
- Claim: Fe2(P1-xSix) with x ≈ 1/3 stays easy-axis with K1 > 1 MJ/m³.
- Basis: literature (doi:..., measured K1 2.3 for Fe2P) + our E041 (Fe2P calibrates to 2.38)
- Prior: literature-backed   (or: no-precedent)
- Prediction: K1 1.2–2.0 MJ/m³, easy axis c; Js ≥ 1.1 T
- Test: E052 (MAE on Fe6SiP2), E053 (moments)
- Verdict: (filled in later) supported by E052 (K1 1.83) — but k-mesh unconverged, see L014
```

Status tags: `[open]`, `[supported]`, `[refuted]`, `[inconclusive]`,
`[parked]`. A refuted hypothesis is a result; keep it, with what refuted it.

`Prior: no-precedent` marks a claim the literature search found no support for
either way. Keep at least one open no-precedent hypothesis in every active
campaign; that is where new results come from.

## candidates.md

```markdown
| ID | Formula | Asset | Origin | Novelty | e_hull | Js (T) | K1 (MJ/m³) | Score | Status |
|---|---|---|---|---|---|---|---|---|---|
| C12 | Fe6SiP2 | file:01a1… | built: Fe2P, Si on P2 | computed-only | 0.012 | 1.19 | 1.83 | 0.81 | lead |
```

- Origin: `literature`, `database` (MP/Alexandria/COD id), `ggen` (explore
  action id), `built` (parent + operation), or `generated` (model route).
- Novelty, from the novelty check in `research.md`: `known` (reported
  experimentally), `computed-only` (in a computed database, never reported
  as made), `new` (found nowhere). A `known` candidate can be a calibration
  or a parent, never a discovery.
- Leave unmeasured cells empty. Write `err` for a calculation that failed:
  unknown is not bad.
- Status: `lead`, `measuring`, `parent`, `calibration`, `dropped (<why>)`.

## experiments.jsonl

One JSON object per line. Append the line when the experiment starts, with
`status: "running"` and the action id, then append a second line with the
same `id` when it finishes. The last line for an id wins. Never rewrite
earlier lines.

```json
{"id":"E052","date":"2026-10-31","campaign":"re-free-magnet","hypothesis":"H7","candidate":"C12","kind":"measure","route":"mmoderwell/magnetic-anisotropy-energy","input_assets":{"structure":"01a1..."},"params":{"kspacing":0.16},"action_id":"01a2...","status":"running","prediction":{"property":"K1","low":1.2,"high":2.0,"unit":"MJ/m3","note":"easy axis c"},"prior":"literature-backed"}
{"id":"E052","date":"2026-10-31","status":"done","result":{"property":"K1","value":1.83,"unit":"MJ/m3","note":"easy axis c"},"output_assets":["01a2..."],"cost_usd":4.12,"verdict":"supports","surprise":0.2}
```

- `kind`: `literature`, `novelty`, `explore` (GGen or other generator),
  `build` (substitution, defect, strain, supercell), `relax`, `measure`.
- `status`: `running`, `done`, `failed`. Failed means the value is unknown;
  record the error and never count it as a negative result.
- `verdict` against the hypothesis's prediction: `supports`, `refutes`,
  `inconclusive`, `n/a`.
- `surprise`: 0 = as predicted, 1 = nothing like it.
- `prior` is copied from the hypothesis, so calibration can compare
  literature-backed and no-precedent predictions.

`scripts/notebook.py calibration <campaign>` reads this file and reports how
often predictions held. `scripts/notebook.py open` lists experiments still
running across all campaigns.

## log.md

Newest entry at the top. One entry per session, written during the session.

```markdown
## 2026-10-31

Read: H7 still open; E049 MAE finished overnight.
Did: novelty check on Fe-Si-P (E050); built Fe6SiP2 from Fe2P (E051); MAE (E052).
Learned: Fe6SiP2 keeps easy axis c (E052). L014 added.
Decided: next, test whether the gain survives a converged k-mesh before any more substitutions.
Next: E052 converged rerun at kspacing 0.12; open H8 (Ge on P, no-precedent).
In flight: E053 (action 01a2..., magnetic moments, started 14:05).
```

`Next` and `In flight` are what the next session starts from. Keep them exact.

## learnings.md

Lessons that hold beyond one campaign: how a route behaves, where a model is
biased, what a region of chemistry does, which search tactics pay. A lesson
about one candidate belongs in that campaign's hypotheses, not here.

```markdown
## Routes and measurement
- L014 MAE at kspacing 0.3 vs 0.2 ranks 13 GGen cells almost at random
  (Spearman 0.09). Treat any K1 from a coarse mesh as a sign, not a value.
  Evidence: tmp/dos_calibration_2026-10-09/ggen. Confidence: high. 2026-10-09
```

- Number lessons `L###` and never reuse a number.
- Each lesson states the claim, the evidence (action ids, asset ids, files,
  papers) and a confidence (`high`, `medium`, `low`), with the date.
- When a lesson turns out wrong, do not delete it. Add
  `Superseded by L0xx (<why>)` on the old one, so the record shows what we
  used to believe.
- Sections: `Routes and measurement`, `Models and their biases`,
  `Chemistry and physics`, `Search tactics`.

## Committing

At the end of each session, commit the notebook in its own repository:

```bash
git -C lab/notebook add -A
git -C lab/notebook commit -m "<campaign>: <one line on what the session found>"
```

The notebook is private and never pushed to the scientist repo.
