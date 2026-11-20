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
  instruments.md              what each route has been shown to do, family by family
  learnings.md                lessons that hold beyond one campaign
  campaigns/
    <slug>/
      campaign.md             the state page: goal, objective, budget, status, best so far, map of families
      hypotheses.md           claims under test and their verdicts
      candidates.md           every structure considered, with novelty and scores
      experiments.jsonl       one line per experiment, appended, never rewritten
      log.md                  one dated entry per session
      work/<experiment id>/   scripts, local CIFs and raw outputs behind one experiment
```

Ouro holds the evidence: structures, route runs, outputs, posts. The notebook
holds the reasoning and points at the evidence by id. Never paste numbers into
the notebook without the action or asset id they came from.

## State and history

Two kinds of file, kept differently:

- **State** says where things stand now: `index.md`, `instruments.md` and
  `campaign.md`. It is rewritten in place and stays small, because every
  session reads all of it. Never append a per-loop paragraph to a state
  file.
- **History** says what happened: `log.md`, `experiments.jsonl`,
  `hypotheses.md`, `candidates.md`, `learnings.md`. It only grows, and no
  session reads it whole.

`notebook.py check` reports a state file that has grown past its size.

## Reading the notebook

```bash
python lab/skill/scripts/notebook.py brief <slug>       # the state page, open hypotheses, live candidates, last log entry, in flight
python lab/skill/scripts/notebook.py learnings          # every lesson, one line each
python lab/skill/scripts/notebook.py recall Fe5Si3      # everything on a topic
python lab/skill/scripts/notebook.py recall L177 H145   # an entry by id, and everything that cites it
```

`recall` takes words and ids. Words must all match one entry, so give it
one topic per call: `recall Fe3Sn carbon` narrows, it does not look up two
things. Ids on their own are separate lookups, and an id you name comes
back whole. A formula matches where an element ends (`Fe3C` does not find
`Fe3Co`), and `Fe5Si3` also finds `Fe₅Si₃`. It prints the matching lessons
in full, then register rows, then, per campaign, the hypotheses (heading
and verdict), candidate rows, and log entries (the lines that match). `--campaign <slug>` keeps it to one campaign, and
`--limit <n>` shows more than the best ten of each. Open a history file
directly only to read one entry `recall` pointed you to.

## index.md

```markdown
| Campaign | Goal | Status | Best so far | Last session |
|---|---|---|---|---|
| [re-free-magnet](campaigns/re-free-magnet/campaign.md) | RE-free permanent magnet, K1 > 1 MJ/m³ uniaxial, Js > 1 T | active | Fe6SiP2: K1 1.83, Js 1.19 (unconverged) | 2026-10-31 |
```

One row per campaign. Set it with
`notebook.py index <slug> <status> "<best so far>"`, which replaces the row
and dates it today. Best so far is the standing candidate with its headline
numbers, or "none yet", in at most 400 characters; what each loop found is
in `log.md`.

Status is one of `active`, `waiting` (only long runs left in flight),
`met`, `blocked`, `parked`.

## instruments.md

The register of what each route has been shown to do. It belongs to the
notebook, not to a campaign: a calibration in one campaign binds the next.

```markdown
| Route | Family | Protocol | Reference | Route reads | Trust | Evidence |
|---|---|---|---|---|---|---|
| Anisotropy | ThMn₁₂ type, iron-only (YFe₁₁Ti) | moment held at the measured value | +2.07 MJ/m³ measured at 5 K | +2.04 | calibrated: at the held moment | L220 |
| Anisotropy | ThMn₁₂ type, iron-only (YFe₁₁Ti) | free moment | +2.07 MJ/m³ measured at 5 K | −0.22 to −0.31 | fails: wrong sign, because the PBE moment is 15 % high | L006, L220 |
```

- One row for one route in one structure family on one protocol. A second
  protocol (another basis, a held moment) is a second row.
- Reference is a made compound's measured value where there is one. An
  all-electron or published-PBE value is second best: say which it is.
- Trust starts with `calibrated` (quote the number, to the error shown),
  `biased` (the direction and rough size of the error are known: say
  them) or `fails` (the route does not reproduce the sign or the size
  here, so a reading is not a measurement), then the detail.
- No row means uncalibrated. A family with no made, measured compound gets
  a row saying "no reference", so the gap is on record.
- Rewrite a row when a calibration changes it, in the session the result
  lands. The reasoning goes in a lesson; the row cites it.
- `notebook.py brief` prints the register, and `notebook.py recall <family>`
  returns its rows first after the lessons.

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
- Where runs go: org <name/id>, team <name/id>, visibility left unset (the team's audience)

## Status
<one paragraph: where the goal stands, judged only from measurements —
met / not yet (what is missing) / blocked (why)>

## Best so far
<the standing candidates only, one bullet each: candidate id, formula, asset
id, the measured values, what is still unmeasured. A cell leaves the list
when it is dropped or beaten.>

| Candidate | Rung | Instrument | Stability | Settings | Arrangement | Makeability | Novelty |
|---|---|---|---|---|---|---|---|
| C323 ZrFe₁₀CoTi | measured | open: 1:12 readings are calibrated only at a held moment (L220) | open: 25.6 Orb / 41.9 PBE references, no PBE check; phonons stable (E1148) | open: DZP only | open: one arrangement | open: nothing with only Zr and Ti on this frame is made | new composition (E1150); nothing in the literature (E1141) |

## Map
| Family | State | Best cell and what it reached | What closed or holds it | Loops | Lessons |
|---|---|---|---|---|---|
| Hexagonal Fe₅Si₃ with B or C in its Fe₆ holes | closed | C334 Fe₁₀Si₆B: hull 41 Orb / 52 PBE references | Parent and filled cell are both easy-plane (−2.14, −2.28 MJ/m³) | 40 | L234, L235 |

## Levers
| Lever | What it did | Works in | Fails in, or what limits it | Untried where it could apply | Lessons |
|---|---|---|---|---|---|
| Electron count on the iron sites of a 1:12 cell | K is a dome against moment: Co adds in the Ti host (ZrFe₉Co₂Ti 1.25–1.78 MJ/m³) and subtracts in the Si host | Zr 1:12, Ti host | Co cannot be pinned to one site; the Ti host is +26 on PBE | Fewer electrons than Fe in the Si host (Mn, if it stays parallel). Against it: hull 26.9 on Orb, and no free route settles Mn's order | L220, L221, L232 |
```

`campaign.md` is the campaign's state page, and `notebook.py brief` prints
all of it. Keep it under 28,000 characters.

- Budget holds the standing terms: the cap, what needs the user's word, and
  where runs go. What a loop spent goes in its log entry and the ledger.
- Status and Best so far are rewritten at the end of every session, not
  added to. If the status did not change, leave it.
- The claims table under Best so far is the claim ladder (SKILL.md) for
  each standing candidate that is put forward as new. Rung is `lead`,
  `measured` or `met`. Each check cell says what the check rests on, with
  the experiment or lesson id: what passed, `open: <what is missing>`,
  or `n/a: <why it does not apply>`. A candidate is `met` only when no
  cell is open, and `notebook.py check` holds the notebook to that: a
  `met` status in `candidates.md` or `index.md` without a `met` row here
  is reported. Update a cell in the session its check lands. A check that
  fails ends the claim: the row leaves the table, and the candidate's
  bullet and its Map row say what failed.
- The Map has one row for every family or thread the campaign has opened.
  State is `open`, `closed` or `parked`, with the reason in a few words
  ("closed on the hull", "parked: instrument"). "What closed or holds it"
  names the result, with its experiment or lesson id, that a later session
  would have to answer before reopening the family. Rewrite a family's row
  when its state changes, and add a row when you open a family. A closed
  family stays on the map: that row is what stops the next session opening
  it again.
- Levers has one row for each change that moved a property for a reason
  you can state: the mechanism, not the cell. The Map says where the
  campaign has been; Levers says what it has learned to do, and a lever
  outlives the family it was found in. "Fails in, or what limits it" is
  what a new host has to avoid. "Untried where it could apply" lists only
  hosts a result or an earlier `Next` actually named, each with the
  strongest thing the notebook holds against it (its cost, a paper, why an
  earlier session ranked it last): a bare "untried" reads as a
  recommendation. When none is named, write "none named", and treat
  finding one as a move (Transfer, in SKILL.md).

## hypotheses.md

A hypothesis is a claim that an experiment can prove wrong. Write the
prediction before the test runs; never edit it afterwards.

```markdown
## H7: Si on the P site of Fe2P keeps uniaxial anisotropy [open]
- Claim: Fe2(P1-xSix) with x ≈ 1/3 stays easy-axis with K1 > 1 MJ/m³.
- Basis: literature (doi:..., measured K1 2.3 for Fe2P) + our E041 (Fe2P calibrates to 2.38)
- Prior: literature-backed   (or: no-precedent)
- Prediction: K1 1.2–2.0 MJ/m³, easy axis c; Js ≥ 1.1 T
- Kill test: phonons on the lowest Si ordering, $0.08 (E051). An unstable cell ends it before any DFT.
- Test: E052 (MAE on Fe6SiP2), E053 (moments)
- Verdict: (filled in later) supported by E052 (K1 1.83) — but k-mesh unconverged, see L014
```

Status tags: `[open]`, `[supported]`, `[refuted]`, `[inconclusive]`,
`[parked]`. A refuted hypothesis is a result; keep it, with what refuted it.

`Kill test` is the cheapest single result that would refute the claim,
with its cost. It runs before the tests that would support the claim.

`Prior: no-precedent` marks a claim the literature search found no support for
either way. Keep at least one open no-precedent hypothesis in every active
campaign; that is where new results come from.

## candidates.md

```markdown
| ID | Formula | Asset | Origin | Novelty | License | e_hull (eV/atom, surface) | Phonons | Js (T) | K1 (MJ/m³, geometry) | Cost ($/kg) | Score | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C12 | Fe6SiP2 | file:01a1… | built: Fe2P, Si on P2 | computed-only | ours | 0.012 (Orb) | stable | 1.19 | 1.50 (DFT-relaxed) | 2.1 | 0.81 | lead |
```

- Give a column to every hard criterion in the objective, plus the measured
  properties that are scored. Adapt the columns to the campaign.
- Say how each value was measured where it matters: the hull surface (Orb
  or PBE), and the geometry behind K1 (unrelaxed, ML-relaxed, DFT-relaxed).
  Never mix bases in one column without saying so.
- Every row has an asset id. A candidate with no structure on Ouro can't be
  measured or cited.
- License: `ours`, or the source license (`CC-BY-4.0`, `CC-BY-NC-4.0`, …).
  Non-commercial rows can be measured by any route; their derived assets keep
  the license and are never priced.

- Origin: `literature`, `database` (MP/Alexandria/COD id), `ggen` (explore
  action id), `built` (parent + operation), or `generated` (model route).
- Novelty, from the novelty check in `research.md`: `known` (reported
  experimentally), `computed-only` (in a computed database, never reported
  as made), `new` (found nowhere). A `known` candidate can be a calibration
  or a parent, never a discovery.
- Leave unmeasured cells empty. Write `err` for a calculation that failed:
  unknown is not bad.
- Status starts with one of `lead`, `measuring` (runs in flight),
  `measured`, `met`, `parent`, `calibration`, `parked`, `dropped`, then
  the reason in brackets: `dropped (easy-plane, E1181)`. `lead`,
  `measured` and `met` are the rungs of the claim ladder, and `met` is
  written only when the claims table in `campaign.md` has the candidate
  there. `notebook.py brief` lists the rows that start with `lead`,
  `measuring`, `measured` or `met`, so when a lead is beaten or fails,
  change its first word.

## experiments.jsonl

One JSON object per line, appended in three steps that share one `id`:
1. `planned`, with the prediction, **before** the route call.
2. `running`, with the `action_id`, straight after the call returns.
3. `done` or `failed`, with the result, when it finishes.

Later lines for an id update earlier ones. Never rewrite earlier lines.
Experiments with no route call (literature, novelty, local builds) can go
straight from `planned` to `done`.

Take every new id from `notebook.py next-id <campaign> E` (or `H`, `C`;
`next-id L` for a learning). It reserves the id (experiments in the
ledger, the others in `.reserved.json`), so a forked session running
beside this one cannot take the same number.
Counting up from the last id you remember is how sun-tetragonal ended up
with seven experiments sharing ids and a spend total $5 short.
`notebook.py check` lists ids used twice.

A batch is one experiment, not one per cell: one `planned` line with the
prediction for the batch (rates, counts), one `done` line with the counts,
and the per-cell action ids in `work/<id>/state.json`, which `screen.py`
writes. Cite a single cell's number by its action id from that file.

```json
{"id":"E052","date":"2026-10-31","campaign":"re-free-magnet","hypothesis":"H7","candidate":"C12","kind":"measure","route":"mmoderwell/magnetic-anisotropy-energy","input_assets":{"structure":"01a1..."},"params":{"kspacing":0.16},"status":"planned","prediction":{"property":"K1","low":1.2,"high":2.0,"unit":"MJ/m3","note":"easy axis c"},"prior":"literature-backed"}
{"id":"E052","status":"running","action_id":"01a2..."}
{"id":"E052","date":"2026-10-31","status":"done","result":{"property":"K1","value":1.83,"unit":"MJ/m3","note":"easy axis c"},"output_assets":["01a2..."],"cost_usd":4.12,"cost_estimated":true,"verdict":"supports","surprise":0.2}
```

A one-sided prediction leaves the other bound `null`, e.g.
`{"property":"Js","low":1.0,"high":null}`.

- `kind`: `literature`, `novelty`, `explore` (GGen or other generator),
  `build` (substitution, defect, strain, supercell), `relax`, `measure`.
- `status`: `planned`, `running`, `done`, `failed`. Failed means the value is
  unknown; record the error and never count it as a negative result.
- `cost_usd`: from the action's `cost` block. Org-paid runs report none, so
  estimate it (seconds × rate in routes.md) and set `cost_estimated: true`.
- `verdict` against the hypothesis's prediction: `supports`, `refutes`,
  `inconclusive`, `n/a`.
- `surprise`: 0 = as predicted, 1 = nothing like it.
- `prior` is copied from the hypothesis, so calibration can compare
  literature-backed and no-precedent predictions.

`lab/skill/scripts/notebook.py calibration <campaign>` reads this file and
reports how often predictions held. `lab/skill/scripts/notebook.py open`
lists experiments still planned or running across all campaigns.

## log.md

Newest entry at the top. One entry per session, written during the session.

```markdown
## 2026-10-31

Chose: option 2 of the last `Next` (Si on P), not option 1 (more Co cells): E049 came back flat overnight, so the Co thread has nothing left to rank.
Read: H7 still open; E049 MAE finished overnight.
Did: novelty check on Fe-Si-P (E050); built Fe6SiP2 from Fe2P (E051); MAE (E052).
Learned: Fe6SiP2 keeps easy axis c (E052). L014 added.
Decided: no more substitutions until the gain survives a converged k-mesh.
Next:
1. Rerun E052 at kspacing 0.12, about $1. The gain is one coarse-mesh number, and L014 says that mesh ranks cells almost at random. Drops if E053 shows the moment collapsed, because then K doesn't matter.
2. Open H8 (Ge on P, no-precedent). Nothing published, same site. Moves to first if the rerun holds, because then the site is the lever.
3. Leave Fe2P for an Fe-Co-B explore (H17). Moves up if the rerun kills the gain.
Hunch, not in the files: the E052 cell relaxed oddly along c; check the c/a before trusting K.
In flight: E053 (action 01a2..., magnetic moments, started 14:05).
```

`Chose` is the first line written in a session: which option of the last
`Next` it took, and why if not the first. `Next` is a ranked proposal for
the next session, each option with its reason, its first test and cost,
and what would change its rank. `In flight` must be exact.

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

- Number lessons `L###` and never reuse a number (`notebook.py next-id L`).
- The first sentence is the claim, and it has to stand on its own: it is
  the one line `notebook.py learnings` prints, and all a later session
  sees unless it asks for the lesson. "Never take K1 from kspacing 0.3."
  works. "Two instrument readings to discount." does not: it says a lesson
  exists without giving it.
- After it come the evidence (action ids, asset ids, files, papers) and a
  confidence (`high`, `medium`, `low`), with the date.
- When a lesson turns out wrong or too broad, do not delete it. Put a note
  in square brackets at its start, naming the lesson that changes it:
  `- L199 [Point (4) corrected by L245, 2026-11-16: <what changed>.] ...`,
  or `Superseded by L0xx (<why>)` when nothing of it stands. The index
  marks such a lesson with the ids that amend it, and the record still
  shows what we used to believe.
- Sections: `Routes and measurement`, `Models and their biases`,
  `Chemistry and physics`, `Search tactics`.

## Committing

At the end of each session, commit the notebook in its own repository:

```bash
git -C lab/notebook add -A
git -C lab/notebook commit -m "<campaign>: <one line on what the session found>"
```

`add -A` takes everything. If `git status` showed changes at the start of
the session that were not yours, tell the user and commit only your own
files.

The notebook is private and never pushed to the scientist repo.
