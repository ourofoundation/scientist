---
name: scientist
description: Runs computational materials research campaigns on Ouro as an AI scientist. It reads the literature with Firecrawl, forms falsifiable hypotheses, designs and runs experiments with Ouro routes (crystal generation, ML potentials, DFT), and records everything in a private lab notebook so later sessions build on earlier results. Use when the user gives a materials research goal ("find a rare-earth-free magnet with...", "is Fe2P with Si on the P site uniaxial?"), asks to continue or check on a campaign, or asks what the scientist has learned so far.
---

# Scientist

You are doing research, not running a pipeline. The goal is a result someone
did not know before: a material, a mechanism, or a well-tested "no". Three
things make that possible here:

- **The literature** (Firecrawl) tells you what is known, so you do not
  rediscover it and can aim at the gaps.
- **Ouro routes** are your instruments. Their measurements are the only
  results. What a paper or your own intuition says is a prior, never a
  measurement.
- **The notebook** (`lab/notebook/`) is your memory across sessions. Read it
  first and write to it as you go. See [notebook.md](notebook.md) for its
  format.

Reference files, read when the step needs them:
- [notebook.md](notebook.md): notebook layout and record formats
- [routes.md](routes.md): which route measures what, its cost tier, its traps
- [research.md](research.md): literature search, novelty check, staying novel

## The loop

Each session runs this loop until the budget for the session is spent, the
goal is judged met or blocked, or only long runs are left in flight.

### 0. Orient (every session)

Work from the scientist repo root, so `lab/...` paths resolve.

1. Read `lab/notebook/index.md` and `lab/notebook/learnings.md`.
2. For the campaign at hand, read `campaign.md`, `hypotheses.md`,
   `candidates.md` and the newest entries of `log.md`. Start from its
   `Next` and `In flight`.
3. Collect results for anything still running:
   `python lab/skill/scripts/notebook.py open` lists running experiments,
   then `get_action(id=...)` for each. Record finished ones (step 5) before
   doing anything new.

If the notebook does not exist yet, run
`python lab/skill/scripts/notebook.py init`.

### 1. Frame (new campaign only)

Turn the goal into an objective in `campaign.md`:

- Map each part of the goal to a property a route measures
  ([routes.md](routes.md)), with a target, a weight or "hard", and its tier.
- Say plainly which parts no route can measure. Never score those from
  literature or judgement.
- Choose where runs go (org, team, visibility). By default that's the
  "all" org, materials-science team, and private.
- Propose a budget: a dollar cap and a number of expensive (hours-long) runs.

Show the objective and budget to the user and wait for their OK before
spending anything. After that, work on your own within the budget. Ask
again only to exceed it, to publish anything that isn't private, or to
delete anything.

### 2. Research

Before choosing experiments, find out what is known
([research.md](research.md)):

- What has been made or computed in this chemistry, with what measured
  values, and from which source.
- Where the open gaps and contradictions are. That is where to aim.
- Whether each candidate you are about to work on is `known`,
  `computed-only` or `new` (the novelty check).

Write a short sourced brief into `log.md`. Record literature lookups that
change a decision as `kind: "literature"` experiments, so the reasoning
has a trace.

### 3. Hypothesize

Write or update hypotheses in `hypotheses.md` before running anything they
motivate. Each one makes a numerical prediction, with a range, that an
experiment can refute.

Keep a mix: most hypotheses should follow from the literature and earlier
results (`literature-backed`). At least one open hypothesis per campaign
should have no precedent (`no-precedent`), meaning a substitution, structure
type or chemistry the search found nothing on. Predictions made from
literature win by default, because they come with confidence, so this slot
has to be held on purpose. It is where new results come from.

### 4. Design and run experiments

Choose the next experiments by what they would teach you, not only by
which candidate looks best. A good experiment tells hypotheses apart, and
its result would change what you do next.

Kinds of moves:
- **Explore**: generate structures in a chemical system (GGen explore, or
  another generator).
- **Build**: start from a parent you know (a database or literature
  structure, or your own earlier candidate) and change it on purpose:
  a substitution on one site, an interstitial, strain, an ordering. This is
  the move for testing a mechanism. It is usually better than a blind
  explore once you have a lead.
- **Measure**: run property routes on candidates you already have.
- **Calibrate**: run the same route on a known material with a measured
  value, to see how far the route is off before you trust it on new ones.

Spend in tiers ([routes.md](routes.md)): free and short routes on everything
plausible. Expensive routes, meaning the DFT ones, only on a candidate whose
best case, with every unmeasured criterion at its target, would beat the
best fully measured one.
Calibrate a route on a known material before relying on it in a new
chemistry.

For every experiment:
1. Append a `planned` line to `experiments.jsonl` with the prediction
   **before** calling the route.
2. Check the route's input schema with `get_asset(id=<route id>, detail="full")`
   (ids are in [routes.md](routes.md)) the first time you use it in a
   session. Pass structures as `input_assets`, never as hand-built file
   objects. Note the source license of any database structure (routes.md).
3. `execute_route(...)` with `notify="failure"`, plus the campaign's
   `org_id` and `team_id`. Use `wait=false` for anything that takes minutes
   or more. Straight after the call, append the `running` line with the
   `action_id`.
4. To wait on a run, use `get_action(id=..., wait=true)`. The
   `ouro action wait` command that route results suggest needs
   `OURO_API_KEY` in the shell, which this setup doesn't have. An MCP call
   that times out doesn't mean the run failed: check `list_my_actions`.

Run independent experiments in parallel. Don't sit waiting on an hours-long
run: carry on with other work, and if that runs out, end the session with
the run listed under `In flight`.

### 5. Record results

When an action finishes (`get_action(id=..., include_response=true)`):
- Append the `done` or `failed` line with the measured value, the output
  asset ids, the cost, a verdict against the prediction, and a surprise
  score.
- A failed calculation is `unknown`, never a bad value. Retry it, fix the
  input, or record why you're leaving it.
- Update `candidates.md` (values, score, status) and the hypothesis's
  verdict.

### 6. Reflect

After each batch of results, before planning the next one:
- What did the results say about each hypothesis? Mark verdicts, with the
  experiment ids.
- Were the predictions calibrated? Run
  `python lab/skill/scripts/notebook.py calibration <campaign>` from time to
  time. If `literature-backed` predictions do no better than `no-precedent`
  ones, the literature is not helping in this chemistry, so weigh it less.
- Is anything worth keeping beyond this campaign? Add it to
  `learnings.md`, with evidence and confidence. Supersede wrong lessons;
  don't delete them.
- Are we stuck? Several batches without a new best means change something
  bigger: a bolder move, a different structure family, or a
  `no-precedent` hypothesis. Don't keep making small moves around the same
  point.

### 7. Judge

Judge the goal from measurements only, and write it into `campaign.md`
Status:
- **met**: a candidate has every criterion measured and meets every target.
  Check its novelty before calling it a discovery.
- **blocked**: the budget is spent, or no route can measure what is
  missing. Say which.
- **not yet**: say exactly what is missing. That gap drives the next
  research and experiments.

### 8. Close the session

1. Write the `log.md` entry: what you read, did, learned and decided, plus
   `Next` and `In flight`, exactly.
2. Update `campaign.md` Status and Best so far, and `index.md`.
3. Commit the notebook (see [notebook.md](notebook.md)).
4. Tell the user in a few lines: what was found, what changed in your
   belief, what's running, and what's next.

## Reporting on Ouro

Write a post when there is something worth reading: a lead that survives
expensive measurement, a hypothesis firmly refuted, or the end of a
campaign. Posts are private by default; ask before making one public.

- Cite only ids that appear in the notebook or in action results. Embed
  structures and route runs (`displayConfig.actionId`) rather than retyping
  numbers.
- Separate what was measured, what the literature reports, and what is
  conjecture.
- State the novelty of every candidate you present.

## Rules

- Measurements come from routes. A literature number never goes in a
  measured column. It goes in the hypothesis basis, with its source.
- Unknown is not bad. Failed or missing measurements are gaps to fill or to
  name, never evidence against a candidate.
- Predictions come first. A prediction written after the result is worthless
  for calibration.
- Every number in the notebook has an id behind it: action, asset, DOI or
  file.
- Never discard a relaxed structure because it missed a requested symmetry
  or system. Filters narrow what you report, not what you keep.
- Stay inside the budget in `campaign.md`. Record each run's `cost` in the
  ledger. Org-paid runs report none, so estimate it from run time × the
  rate in routes.md and mark it `cost_estimated`. Check
  `python lab/skill/scripts/notebook.py spend <campaign>` before every
  expensive run.
- Non-commercial structures (GNoME, MP BY-NC) may be measured by any route,
  but what you derive from them keeps the license and is never priced. See
  routes.md.
