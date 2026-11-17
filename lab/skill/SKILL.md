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
- **The notebook** (`lab/notebook/`) is your memory across sessions. It is
  too large to read whole, so you read it in tiers (step 0) and write to it
  as you go. See [notebook.md](notebook.md) for its format.

Reference files, read when the step needs them:
- [notebook.md](notebook.md): notebook layout and record formats
- [routes.md](routes.md): which route measures what, its cost tier, its traps
- [research.md](research.md): literature search, novelty check, staying novel
- [reporting.md](reporting.md): how to write the loop post on Ouro

Scripts in `scripts/` (usage with `-h`; routes.md says when to use each):
`notebook.py` to read and keep the notebook, and `crystal.py`, `screen.py`
and `pbe_check.py` for the crystal database, batch screens and PBE
stability checks.

## The loop

Each session runs this loop until the budget for the session is spent, the
goal is judged met or blocked, or only long runs are left in flight.

### 0. Orient (every session)

Work from the scientist repo root, so `lab/...` paths resolve.

A lesson you did not read cannot stop you repeating it, and a long
campaign's notebook is several times what a session can hold. Most of the
avoidable mistakes in the logs were of one kind: the notebook already had
the answer (a family closed, a calibration that failed, a mechanism filed
under another family) and the session did not look. So read in tiers, and
never open `learnings.md`, `hypotheses.md`, `candidates.md` or `log.md`
whole.

1. `python lab/skill/scripts/notebook.py brief <campaign>` prints what
   every session reads: the campaign's state page (objective, budget,
   status, standing candidates, and the map of every family it has opened),
   the open hypotheses, the live candidates, the last log entry, and what
   is in flight. `In flight` is what to collect, and the entry's `Next` is
   the last session's proposal; the `Next` of the three entries before it
   follow, because an option ranked second then is often still open.
   `lab/notebook/index.md` lists the campaigns. The packet runs to tens of
   thousands of characters, more than a shell call shows: write it to a
   file in your scratch directory and read the file.
2. `notebook.py learnings` prints every lesson as one line. Read the whole
   list, the same way. A line is the claim only; its numbers and limits
   are in the full text.
3. Collect results for anything still running:
   `python lab/skill/scripts/notebook.py open` lists running experiments,
   then `get_action(id=...)` for each. A batch has no action id of its
   own: its ids are in `work/<experiment id>/state.json`. Record finished
   ones (step 5) before doing anything new. Read any new comments on the
   newest loop post the log names (`list_comments`); a loop may have none.
   Run `git -C lab/notebook status --short`: changes you did not make are
   someone else's work in progress, so tell the user before your closing
   commit sweeps them in.
4. Decide what this session does. The session that wrote `Next` knew the
   detail but was anchored on its own thread, and it ranked the options
   before these results and comments existed. Check each option against
   what has landed since, anything the user has said, and the whole
   campaign (step 6's "are we stuck?"). Before you take one, recall what
   it stands on, one topic per call: `notebook.py recall <family>`, then
   the parent compound, then the route it leans on. Each returns the
   lessons in full, the register rows, and the hypotheses, candidates and
   log entries that touched it. An option whose family the map shows
   closed needs a reason the closing result does not answer. Then take the
   top option or override it. Open this session's `log.md` entry with a
   `Chose:` line saying which, and why if it isn't option 1.

   When the user names the direction, that is the choice: `Chose:` records
   their words and how they sit against the last `Next`. When they ask for
   something new without naming it, `Chose:` states the question, and the
   family is picked after step 2's research, not before it.

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
experiment can refute. Each one also names its kill test: the cheapest
single result that would refute it, and what that costs. A hypothesis with
no kill test you can name is not testable yet. The kill test runs first.

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
- **Transfer**: take a lever from the campaign's Levers table
  (`campaign.md`) and apply it in a host where it has not been tried. A
  lever is a change that moved a property for a reason you can state.
  Choose the host by what made the lever work and by what stopped it last
  time: if an interstitial gave the anisotropy and then left for a rival
  compound, look for the same site in a host where no rival exists. The first
  campaign's candidates all came from a mechanism applied on purpose; its
  wide screens returned known structure types. A host the table lists as
  untried is a prompt to recall it, not a recommendation: the row is one
  line and may leave out why an earlier session passed it over.
- **Measure**: run property routes on candidates you already have.
- **Calibrate**: run the same route on a known material with a measured
  value, to see how far the route is off before you trust it on new ones.

Spend in tiers ([routes.md](routes.md)): free and short routes on everything
plausible. Expensive routes, meaning the DFT ones, only on a candidate whose
best case, with every unmeasured criterion at its target, would beat the
best fully measured one.

**Instruments first.** `lab/notebook/instruments.md` records what each
route has been shown to do in each structure family; `brief` prints it.
Before you rely on a route in a family, find its row for the protocol you
will use (basis, mesh, geometry):

- `calibrated`: quote the number, with the error the row gives.
- `biased`: the row says in which direction and by how much. Quote the
  number with that correction, or use it only to rank cells inside the
  family.
- `fails`: a reading from this route in this family is not a measurement.
  Use another route, fix the instrument, or name the criterion unmeasured.
- No row: the family's first experiment is the calibration. Run the route
  on a made compound of the family with a measured value, on the protocol
  the candidates will get, before building any candidate. It usually costs
  less than one candidate, and more than one family here was opened and
  reported on before a calibration showed the route could not read it.
  If the family has no made compound with a measured value, write the row
  with "no reference" and carry "uncalibrated" beside every number.

A row covers its protocol only. Most rows were measured in TZDP and the
default basis is DZP, so a TZDP row does not license a DZP number: either
calibrate the parent in the basis you will use, which is the family's
first DFT run, or ask the user for the row's basis. A calibration that
lands goes into the register in the same session: add the row or rewrite
it.

**The cheapest decisive test first.** Open a family, or a series of builds
on one parent, in this order. Each step can close it before the next one
spends anything:

1. Recall (step 0): has the notebook already closed it, or its parent?
2. The parent in the literature, read in full: the measured sign and size
   of the property you want, how the compound is made, and what it turns
   into on cooling. Read the paper, not the abstract: "uniaxial" in an
   abstract has meant a hard axis.
3. The makeability gate, when the parent exists only in a hot window or
   the cell carries an additive. It costs cents.
4. The made parent on the route, on the protocol the candidates will get.
   This is the calibration the register needs, and it is the parent's own
   reading: if the parent has the wrong sign and nothing you hold says
   your change will turn it, stop here.
5. Free screens on the whole pool: Orb hull, Prophet order.
6. Phonons on what is left, before any DFT relaxation.
7. DFT on the survivors, and the quick setting that can already tell the
   outcomes apart before the fine one.

The order is by cost, not by what you most want to know. The commonest
mistake in the logs is a later step run before an earlier one that would
have closed the family: "the calibration should have been the first cell,
not the last"; "one run on the parent, $0.2, would have shown the hard
axis". Skip a step only when it does not apply, and say so in the log.

A step that applies but that the session may not spend on (a free-routes
loop, DFT without the user's word) is not skipped and not jumped over: run
the steps you are allowed, then stop and ask for the next one, with its
cost. So a free loop in a family with no register row ends in a screened
pool and an ask. It cannot end in a property number, and should not try
to.

For every experiment:
1. Take its id from `notebook.py next-id <campaign> E`, and append a
   `planned` line to `experiments.jsonl` with the prediction **before**
   calling the route. A batch of cells is one experiment
   ([notebook.md](notebook.md)).
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
   `OURO_API_KEY` in the shell; the repo's `.env` holds it, so source that
   first if you use the command. An MCP call
   that times out doesn't mean the run failed: check `list_my_actions`.
   For a batch, `screen.py` and `pbe_check.py` keep every action id on
   disk and resume from it, so one background run is the only waiter you
   need. Don't stack wait loops on the same condition.

Run independent experiments in parallel. Don't sit waiting on an hours-long
run: carry on with other work, and if that runs out, end the session with
the run listed under `In flight`.

### Searching for new compounds

When the goal is new stable compounds rather than a property of known
ones, the experiment is a funnel, and most of what comes out of its wide
end is not a compound. Run it in this order:

1. **Pool.** Substitute into near-hull structures that someone has made
   (`crystal.py prototypes find --made`), or change a lead on purpose (fill
   a cage, put a second metal on one of two distinct sites, swap a
   metalloid). In intermetallic systems the databases already cover, blind
   generator explores have not paid; read the search-tactics learnings
   before spending on one.
2. **Screen** with `screen.py run`: free, Orb relaxation and hull distance,
   nothing becomes an asset.
3. **Classify** the cells within 25 meV/atom with `crystal.py classify`
   before counting anything. Lattice decorations and vacancy-ordered
   decorations are ordered alloys; report them as that. `unmatched` cells
   are the candidates for a new type. An `ordering` is a known type with
   two elements on one sublattice, and the label is a fact, not a verdict:
   most are one composition of a solid solution (Laves phases with Fe and
   Co mixed), a few are compounds (Fe3Ge2Al on CoSn-type FeGe). It is a
   candidate only when it sits below the line between its parent and the
   other end member, or the site preference is large; measure that before
   calling it either.
4. **Rank** the candidates on the published-PBE surface (`screen.py pbe`),
   and search the literature for each formula and its structure type.
5. **PBE check** (`pbe_check.py`) on what is left: competitors from both
   hulls, plus any competitor no database lists that you can build. This is
   DFT: it needs the budget, and the user's word where the campaign says so.
6. **Phonons** last, on what survived PBE.

Say where a cell is in the funnel every time you name it. On the Orb hull
alone it is a "lead, ML-only", never "stable": Orb has been 30 meV/atom
wrong in both directions on new cells, and four of the first seven cells
put through a PBE check failed it. Each stage's counts go in the post,
including how many cells each check removed.

### 5. Record results

When an action finishes (`get_action(id=..., include_response=true)`):
- Append the `done` or `failed` line with the measured value, the output
  asset ids, the cost, a verdict against the prediction, and a surprise
  score.
- A failed calculation is `unknown`, never a bad value. Retry it, fix the
  input, or record why you're leaving it.
- Update `candidates.md` (values, score, status) and the hypothesis's
  verdict. If the result is a calibration, update `instruments.md`; if it
  is one of a standing candidate's checks, update its row in the claims
  table.

### 6. Reflect

After each batch of results, before planning the next one:
- What did the results say about each hypothesis? Mark verdicts, with the
  experiment ids.
- Were the predictions calibrated? Run
  `python lab/skill/scripts/notebook.py calibration <campaign>` from time to
  time. If `literature-backed` predictions do no better than `no-precedent`
  ones, the literature is not helping in this chemistry, so weigh it less.
- Is anything worth keeping beyond this campaign? Add it to
  `learnings.md`, with evidence and confidence. Its first sentence is all
  a later session sees until it asks for more, so make that sentence the
  claim. Supersede wrong lessons; don't delete them.
- Did a result show a lever: a change that moved a property, for a reason
  you can state? Add it to the Levers table in `campaign.md`, or update
  its row with where it now works, where it fails, and where it has not
  been tried. A lever found in a family that then closed is still a lever.
- Are we stuck? Several batches without a new best means change something
  bigger: a bolder move, a different structure family, or a
  `no-precedent` hypothesis. Don't keep making small moves around the same
  point. Read the Levers table before saying nothing is left: a row with
  an untried host is a move the campaign already has a reason for, once
  recall has not turned up why it was left.

### 7. Judge

Judge the goal from measurements only, and write it into `campaign.md`
Status:
- **met**: a candidate is on the `met` rung of the claim ladder below.
- **blocked**: the budget is spent, or no route can measure what is
  missing. Say which.
- **not yet**: say exactly what is missing. For a candidate on the
  `measured` rung that is its open checks, by name. That gap drives the
  next research and experiments.

### The claim ladder

A number that meets a target is not yet a result. What turns it into one is
surviving the checks that could break it, and those checks are cheap next
to building on a claim that later falls. Every standing candidate has a row
in the claims table of `campaign.md` ([notebook.md](notebook.md)) and
stands on one of three rungs. Name the rung whenever you name the
candidate: in the notebook, to the user, and in a post.

- `lead`: at least one criterion of the objective has no measurement by
  the route the objective names for it, or has only a screen standing in
  for that route (a Prophet moment where the objective asks for DFT).
- `measured`: every criterion has been measured by its route in the
  objective, on the candidate's own relaxed cell, and is inside the
  objective's target. An objective that scores stability on the Orb hull
  is satisfied by an Orb hull here; the Stability check below is what
  replaces it with PBE.
- `met`: measured, and each check below has passed or is marked not
  applicable with the reason.

The checks, which are the table's columns:

- **Instrument.** Each headline number comes from a route whose register
  row for this family and protocol is `calibrated`, or `biased` with the
  correction applied.
- **Stability.** The hull distance comes from a PBE check with every
  competitor in one setup (`pbe_check.py`), after that setup reproduced
  published reactions that between them contain each phase the candidate
  decomposes into: a check on other phases of the same elements says
  nothing about the ones in the balance. An Orb hull, or published-PBE
  references with the cell scored by Orb, does not pass this. The PBE
  number is judged against the user's screening line where `campaign.md`
  records one, otherwise against the objective's target, and a margin
  smaller than the setup's own error on its reference reactions is not
  settled either way. Phonons are run on the cell the properties were
  measured on.
- **Settings.** The number stays inside its target when each setting the
  route is sensitive to in this family is tightened: basis, k-mesh, the
  band-sum smearing, and the spin moment where the register says PBE's is
  off.
- **Arrangement.** The claim belongs to the composition, not to one cell:
  the lowest-energy arrangement of the substituent found is the one
  measured, or a second arrangement agrees, and the magnetic ground state
  was compared with the competing orders.
- **Makeability.** The cheap gate: what the parent turns into on cooling,
  and whether the additive has somewhere it would rather be. Passing means
  nothing rules the compound out, not that it can be made.
- **Novelty.** The novelty route on the final cell, and a literature
  search for the formula and the structure type ([research.md](research.md)).

**Harden before you explore.** When a candidate reaches `measured`, the
next experiments are its open checks, before any new family is opened.
Order them by what is cheapest and most likely to fail. The first campaign
called a cell met for 24 loops, through posts and a write-up, before its
stability check ran; the check came back unsettled.

The rule has three ways out, so it cannot pin a campaign to one cell:

- A check fails. The claim is over: take the row out of the table, and
  say what failed in the candidate's bullet and in its Map row. A cell
  with a failed check is not hardened any further.
- A check cannot be run now: it needs the user's word, a budget the loop
  does not have, or a route that does not exist. Ask once, with the cost.
  Until the answer comes the candidate stays on `measured` with the open
  check named in Status, and the session is free to explore.
- The user asks for exploration. Say in one line which checks stay open
  on which cell, then explore.

`notebook.py check` reports any place the notebook says met and the table
does not.

### 8. Close the session

1. Write the `log.md` entry: what you read, did, learned and decided, plus
   `Next` and `In flight`, exactly. `Next` is a proposal for the next
   session to judge, not a decision: two or three options, ranked, each
   with the reason for it, its first test and what that costs, and the
   result that would change its rank.
   Include one that leaves the current thread whenever there is one worth
   naming. Add anything the next session needs that the other files don't
   hold, such as a hunch or why a run failed. A bare to-do gets followed
   blindly, so give the reasons.
2. Bring the state page up to date. Rewrite `campaign.md` Status and Best
   so far (the claims table included) as they stand now, the Map row of
   each family this session touched (a new family gets a row), and any
   Levers row the session changed. These say where the campaign is, not
   what happened: what happened is the log entry you just wrote, so nothing
   is appended here. Set the campaign's row in `index.md` with
   `notebook.py index <campaign> <status> "<best so far>"`, then run
   `notebook.py check` and fix what it reports from this session.
3. Write the loop's post on Ouro (see "Reporting on Ouro") and put its id
   in the `log.md` entry.
4. Commit the notebook (see [notebook.md](notebook.md)).
5. Tell the user in a few lines: what was found, what changed in your
   belief, what's running, and what you propose next, with a link to the
   post.

## Reporting on Ouro

Every loop ends with one post, even when nothing worked: a refuted
hypothesis or a soft candidate is a result, and the team follows the
campaign through these posts. Publish it where the campaign's runs go
(`campaign.md` Budget: org, team, visibility). Never make one public, or
wider than the campaign's visibility, without asking.

- Title: `<campaign title>, loop <n>: <the finding in a few words>`.
  Number loops by session in `log.md`.
- Open with the verdict, written for someone who reads nothing else: the
  question the loop asked, then a bold line saying whether it found a new
  candidate (yes, with its rung on the claim ladder and its headline
  numbers against the campaign's best, or no, with the reason), then why
  and what the loop was worth. See [reporting.md](reporting.md).
- Then, in order: the measured results (a short table, one row per
  candidate, with the geometry and route behind each value); what each
  hypothesis did (prediction vs result); what changed in our belief.
- Instrument problems and the `Next` proposal stay in `log.md`; they are
  not sections of the post.
- Preview the one to three structures the loop is about as CIFs
  (`viewMode: "preview"`, the crystal viewer). Link everything else inline:
  each measured number to its run (`[1.42](action:<id>)`), other structures,
  earlier loop posts and datasets by id. Never embed a route run as a block.
  See [reporting.md](reporting.md) for the table, the shape and a pre-post
  check.
- Cite only ids that appear in the notebook or in action results.
- Separate what was measured, what the literature reports, and what is
  conjecture.
- State the novelty and license of every candidate you present.
- Keep it short: the notebook holds the detail.

## Rules

- Measurements come from routes. A literature number never goes in a
  measured column. It goes in the hypothesis basis, with its source.
- Unknown is not bad. Failed or missing measurements are gaps to fill or to
  name, never evidence against a candidate.
- A reading from an uncalibrated route is a sign, not a value. Check the
  route's row in `instruments.md` for the family before you quote it.
- "Met" is a rung, not a mood. Say `measured` until every check in the
  claims table has passed.
- Predictions come first. A prediction written after the result is worthless
  for calibration.
- Recall before you commit. Before opening a family, proposing a
  calibration, trusting a route in a chemistry new to it, or telling the
  user nothing is left to try, run `notebook.py recall` on it and read
  what the notebook holds.
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
