"""Annealing search over materials, with an LLM as the proposal distribution.

The harness owns control flow: budget, temperature, move selection, and the
verdict on whether the goal is met. The LLM is called for bounded decisions —
propose moves from the best position so far, triage which candidates deserve a
calculation, reflect on what a step taught us. After every step the harness
judges the goal from the measurements and feeds what is still missing into the
next proposal. Every step becomes a node in a persisted tree, so the path
travelled is context for the next step and for the next run.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from ..computational.catalog import CATALOG, CHEAP, EXPENSIVE, FREE, describe_catalog
from ..computational.explorer import (
    CRYSTAL_SYSTEM_NAMES,
    crystal_system,
    identity_key,
    normalize_system,
)
from ..utils.logging import get_logger
from .agent import run_agent
from .config import ScientistConfig
from .objective import ObjectiveSpec, compile_objective, parse_elements
from .store import Store
from .tools import query_memory_tool, read_url_tool, web_search_tool

logger = get_logger("search")


# -- LLM decision schemas ---------------------------------------------------


class FractionBound(BaseModel):
    element: str
    min: Optional[float] = Field(description="minimum atomic fraction 0-1, or null")
    max: Optional[float] = Field(description="maximum atomic fraction 0-1, or null")


class Move(BaseModel):
    kind: Literal["explore", "deepen", "evaluate"] = Field(
        description="explore: run GGen on a chemical system. "
        "deepen: re-run the from_node's system with different constraints or "
        "more trials. evaluate: measure properties on existing candidates."
    )
    from_node: Optional[int] = Field(
        description="id of the node this move builds on; null for the current node"
    )
    chemical_system: Optional[str] = Field(
        description="dash-separated elements, e.g. Fe-Co-B (explore/deepen)"
    )
    crystal_systems: List[str] = Field(
        description="restrict generation to these crystal systems; empty for all"
    )
    fraction_bounds: List[FractionBound] = Field(
        description="composition bounds per element; empty for none"
    )
    max_atoms: Optional[int] = Field(description="max atoms per cell, or null")
    num_trials: Optional[int] = Field(
        description="generation attempts per stoichiometry, or null"
    )
    candidate_ids: List[int] = Field(description="candidates to measure (evaluate)")
    properties: List[str] = Field(description="properties to measure (evaluate)")
    boldness: float = Field(
        description="0 = small local step from from_node, 1 = jump to an "
        "unrelated region"
    )
    predicted_score: float = Field(
        description="your honest estimate (0-1) of the best candidate score "
        "this move will produce"
    )
    prediction: str = Field(description="what you expect to find, concretely")
    rationale: str = Field(description="why this move, given the path so far")


class Proposals(BaseModel):
    reading: str = Field(
        description="your reading of the search so far: what the path shows "
        "and where the objective is likely to improve"
    )
    moves: List[Move]


class Selection(BaseModel):
    candidate_id: int
    reason: str


class Triage(BaseModel):
    selections: List[Selection]
    notes: str


class Reflection(BaseModel):
    outcome: str = Field(description="what happened, in two or three sentences")
    surprise: float = Field(
        description="0 = exactly as predicted, 1 = completely unexpected"
    )
    learnings: List[str] = Field(
        description="one to three generalisable statements worth remembering "
        "in future steps and future runs; empty if nothing new was learned"
    )


class Finding(BaseModel):
    claim: str = Field(description="one specific fact, with its numbers")
    source: str = Field(description="URL it came from, or 'memory' for our own data")


class ResearchBrief(BaseModel):
    summary: str = Field(
        description="what this means for the next move, in a short paragraph"
    )
    findings: List[Finding]
    cautions: List[str] = Field(
        description="known reasons a tempting direction fails; empty if none"
    )

    def render(self) -> str:
        lines = [self.summary, ""]
        lines += [f"- {f.claim} [{f.source}]" for f in self.findings]
        if self.cautions:
            lines += ["", "Cautions:"] + [f"- {c}" for c in self.cautions]
        return "\n".join(lines)


# -- Prompts ----------------------------------------------------------------

SEARCH_SYSTEM = """You are the scientific intuition inside a materials search.

The search works like simulated annealing. A position is a node: a chemical
system that was explored, the candidate structures found there, and what was
measured on them. At each step you propose several possible next moves from
what is known; a harness picks one and runs the calculations. You are not
running a to-do list — you are estimating, from chemistry and physics and from
everything this search has already measured, which direction improves the
objective.

How the harness works:
- It picks among your moves with probability proportional to
  exp(predicted_score / T). At high temperature bold moves get real chances;
  at low temperature it takes your best estimate. So make predicted_score
  honest rather than optimistic: after each step your prediction is compared
  with the measured result, and that record is shown back to you.
- A score counts only what has been measured: an unmeasured criterion earns
  nothing. Where a candidate is partly measured, the record also shows the
  score it could reach if everything still unmeasured came out perfectly.
- The search always continues from the best node so far. The temperature
  falls over the run's steps and acts only through which move is picked.
- After every step the harness judges the goal from the measurements and
  tells you what is still missing, under "Where the goal stands". Aim your
  moves at that gap. The run ends when a candidate has every criterion
  measured and meets every target, or when the steps run out.

Propose moves that differ meaningfully: span local refinements of the current
position through bolder jumps, in proportion to the temperature. A move may
branch from any earlier node when the record shows that branch was abandoned
too early. An exploration of a system that already ran with constraints at
least as wide is refused — its result is in the record — so widen the cell
size or trial count, or choose different bounds, if you return to a system.

Calculations are the scarce resource. A GGen exploration takes tens of minutes
to hours and measures the free and cheap properties on what it finds. Expensive
properties are multi-hour DFT jobs with a small per-run allowance. They are
spent only by `evaluate` moves, and only on candidates that could still beat
the best fully measured one, so propose an `evaluate` when a partly measured
candidate is promising enough to settle."""

RESEARCH_SYSTEM = """You support a computational materials search by doing
the background work before its next step. You are given the objective and the
search record, and tools to search the literature, read pages, and query the
search's own memory of everything it has measured.

Find what bears on where to go next from the current position: phases reported
in the neighbouring chemical systems and their measured properties, known
reasons a direction fails, structural motifs associated with the target
properties, and patterns in our own measurements that the record's summary
tables do not show. Prefer a few well-sourced specifics over a broad survey,
and skip general background and anything the record already states. You are
informing the choice of the next move, not making it."""

TRIAGE_SYSTEM = """You choose which candidate structures deserve a calculation
in a materials search. You are given the objective and the candidates found at
the current step with everything already known about each. Pick the ones most
likely to score well on the objective once measured, using chemical judgement
— for example, a low-energy phase that cannot have the target property is a
wasted calculation. You may pick fewer than the limit, or none, when the
candidates do not justify the cost. Each line is one distinct structure; where
it notes equivalent candidates, those are the same crystal in other cell sizes
and need no separate calculation."""

REFLECT_SYSTEM = """You record what a step of a materials search taught us.
You are given the move, the prediction made before it ran, and the measured
result. Compare them honestly. A property marked NOT MEASURED had a
calculation error: its value is unknown, which is different from a bad value,
so never report it as a failed check. Likewise an exploration that returned
no candidates shows only that nothing near the hull was generated under that
move's own constraints (composition bounds, cell size, crystal systems, number
of trials): say which constraints were in force, and do not conclude anything
about the chemistry unless the summary shows many stoichiometries were tried.
Learnings are read at every later step and in
later runs, often without the surrounding detail, so write each as a
self-contained statement that names the chemical system, the observation, and
the numbers that support it. Record negative results too: a direction that
failed and why is as useful as one that worked."""


@dataclass
class Budget:
    expensive_left: int
    deadline: float


@dataclass
class Verdict:
    """The harness's judgement of the goal after a step.

    ``met`` and ``blocked`` end a run; ``not_yet`` carries what is missing,
    which is shown to the model before it proposes the next move.
    """

    status: Literal["met", "not_yet", "blocked"]
    reason: str


def campaign_key(goal: str) -> str:
    """Stable key so the same goal text resumes the same campaign."""
    normalized = re.sub(r"\s+", " ", goal.strip().lower())
    return hashlib.sha1(normalized.encode()).hexdigest()[:12]


class Search:
    def __init__(
        self,
        config: ScientistConfig,
        llm,
        store: Store,
        explorer,
        evaluator,
        rng: Optional[random.Random] = None,
        meter=None,
    ) -> None:
        self.config = config
        self.llm = llm
        self.store = store
        self.explorer = explorer
        self.evaluator = evaluator
        self.rng = rng or random.Random()
        # Anything with drain_charges(): reports what Ouro routes cost.
        self.meter = meter

    # -- campaign ---------------------------------------------------------

    def open_campaign(self, goal: str, key: Optional[str] = None) -> Dict[str, Any]:
        """Resume the campaign for this goal, or compile a new objective."""
        key = key or campaign_key(goal)
        campaign = self.store.get_campaign(key)
        if campaign and campaign.get("objective"):
            logger.info(
                f"Resuming campaign '{key}' "
                f"({campaign['steps_done']} steps, best {campaign['best_score']:.3f})"
            )
            return campaign

        if campaign is None:
            campaign = self.store.create_campaign(key, goal)
        objective = compile_objective(self.llm, campaign["goal"])
        logger.info(f"Compiled objective:\n{objective.describe()}")
        self.store.update_campaign(
            campaign["id"],
            objective=objective.model_dump(),
            temperature=self.config.t_initial,
        )
        return self.store.campaign(campaign["id"])

    def run(self, campaign_id: int, run_id: int) -> Dict[str, Any]:
        """Step until the goal is met, nothing more can be done, or the run's
        step budget is spent."""
        budget = Budget(
            expensive_left=self.config.max_expensive_evals,
            deadline=time.time() + self.config.max_runtime_seconds,
        )
        self._save_costs(campaign_id, run_id)  # objective compilation, if any
        verdict = self.judge(campaign_id, budget)
        for index in range(self.config.max_steps):
            if verdict.status != "not_yet":
                break
            if time.time() > budget.deadline:
                logger.info("Run time budget exhausted")
                break
            try:
                self.step(campaign_id, run_id, index, budget, verdict)
            except Exception:
                logger.exception(f"Step {index} could not be taken")
                self._save_costs(campaign_id, run_id)
            verdict = self.judge(campaign_id, budget)
        logger.info(f"Goal {verdict.status.replace('_', ' ')}: {verdict.reason}")
        result = self.result(campaign_id, run_id)
        result["verdict"] = {"status": verdict.status, "reason": verdict.reason}
        return result

    # -- the goal -----------------------------------------------------------

    def judge(self, campaign_id: int, budget: Budget) -> Verdict:
        """Decide from the measurements whether the goal is met, and if not,
        what is missing."""
        objective = ObjectiveSpec.model_validate(
            self.store.campaign(campaign_id)["objective"]
        )
        candidates = self.store.candidates(campaign_id)  # best score first
        for c in candidates:
            if not objective.gaps(c["properties"]):
                return Verdict(
                    "met",
                    f"{c['formula']} (candidate {c['id']}) has every criterion "
                    f"measured and meets every target, score {c['score']:.3f}.",
                )

        complete = [c for c in candidates if objective.complete(c["properties"])]
        proven = complete[0]["score"] if complete else 0.0
        if complete:
            best = complete[0]
            lines = [
                f"Best fully measured: {best['formula']} (candidate {best['id']}), "
                f"score {best['score']:.3f}. It misses the goal on: "
                + "; ".join(objective.gaps(best["properties"]))
                + "."
            ]
        else:
            lines = ["No candidate has every criterion measured yet."]

        waiting = sorted(
            (
                {**c, "ceiling": objective.ceiling(c["properties"])}
                for c in representatives(candidates)
                if c["evaluated"] and not objective.complete(c["properties"])
            ),
            key=lambda c: -c["ceiling"],
        )
        waiting = [c for c in waiting if c["ceiling"] > proven]
        if waiting:
            lines.append("Partly measured candidates that could still beat that:")
            for c in waiting[:5]:
                missing = [
                    name
                    for name in objective.properties()
                    if c["properties"].get(name) is None
                ]
                lines.append(
                    f"- candidate {c['id']}: {c['formula']}, score "
                    f"{c['score']:.3f}, up to {c['ceiling']:.3f} once "
                    f"{', '.join(missing)} is measured"
                )
        else:
            lines.append(
                "No partly measured candidate could beat that: new candidates "
                "are needed."
            )

        expensive = objective.properties(EXPENSIVE)
        queued = [
            c
            for c in waiting
            if any(c["properties"].get(name) is None for name in expensive)
        ]
        if (
            budget.expensive_left <= 0
            and queued
            and len(queued) >= self.config.max_expensive_evals
        ):
            lines.append(
                "The expensive-calculation allowance for this run is spent and "
                f"{len(queued)} candidates already wait for one, which fills "
                "the next run's allowance."
            )
            return Verdict("blocked", "\n".join(lines))
        return Verdict("not_yet", "\n".join(lines))

    def _proven_best(self, campaign_id: int, objective: ObjectiveSpec) -> float:
        """Best score among candidates with every criterion measured."""
        return max(
            (
                c["score"]
                for c in self.store.candidates(campaign_id)
                if objective.complete(c["properties"])
            ),
            default=0.0,
        )

    def _temperature(self, index: int, campaign: Dict[str, Any]) -> float:
        """Fall from t_initial to t_min over the run's steps; reheat on a stall."""
        cfg = self.config
        fraction = index / max(1, cfg.max_steps - 1)
        temperature = cfg.t_initial * (cfg.t_min / cfg.t_initial) ** fraction
        if campaign["steps_since_best"] >= cfg.stall_steps:
            temperature = min(cfg.t_initial, 2 * temperature)
            logger.info(
                f"No new best in {campaign['steps_since_best']} steps — "
                f"reheating to T={temperature:.3f}"
            )
        return temperature

    def result(self, campaign_id: int, run_id: int) -> Dict[str, Any]:
        campaign = self.store.campaign(campaign_id)
        objective = ObjectiveSpec.model_validate(campaign["objective"])
        candidates = self.store.candidates(campaign_id)
        # A fully measured candidate is a result; a partly measured one is a lead.
        complete = [c for c in candidates if objective.complete(c["properties"])]
        return {
            "campaign": campaign,
            "run_id": run_id,
            "nodes": self.store.nodes(campaign_id, run_id),
            "best_candidate": (complete or candidates or [None])[0],
            "usage": self.store.usage(run_id),
        }

    # -- one step ---------------------------------------------------------

    def step(
        self,
        campaign_id: int,
        run_id: int,
        index: int,
        budget: Budget,
        verdict: Optional[Verdict] = None,
    ) -> Dict[str, Any]:
        campaign = self.store.campaign(campaign_id)
        objective = ObjectiveSpec.model_validate(campaign["objective"])
        verdict = verdict or self.judge(campaign_id, budget)
        temperature = self._temperature(index, campaign)
        # The search always continues from the best node so far.
        current = (
            self.store.node(campaign["current_node_id"])
            if campaign["current_node_id"]
            else None
        )

        record = self._record(campaign, objective)
        state = self._state(campaign, objective, index, budget, temperature, verdict)
        research = self._research(record, state, campaign)
        if research:
            state += f"\n\n## Research brief for this step\n{research}"

        moves = self._propose(record, state, campaign, objective, budget)
        move = self._select(moves, temperature)
        parent_id = move.from_node or (current["id"] if current else None)
        logger.info(
            f"Step {campaign['steps_done']} (T={temperature:.3f}): {move.kind} "
            f"{move.chemical_system or move.candidate_ids} "
            f"[predicted {move.predicted_score:.2f}, boldness {move.boldness:.1f}]"
        )

        node_id = self.store.add_node(
            campaign_id=campaign_id,
            run_id=run_id,
            parent_id=parent_id,
            step=campaign["steps_done"],
            kind=move.kind,
            system=move.chemical_system,
            move={
                **move.model_dump(),
                "alternatives": [
                    {
                        "kind": m.kind,
                        "target": m.chemical_system or m.candidate_ids,
                        "predicted_score": m.predicted_score,
                        "rationale": m.rationale,
                    }
                    for m in moves
                    if m is not move
                ],
            },
            temperature=temperature,
            research=research,
        )

        try:
            if move.kind == "evaluate":
                status, score = self._execute_evaluate(
                    move, node_id, campaign, objective, budget
                )
            else:
                status, score = self._execute_explore(
                    move, node_id, campaign, objective
                )
            error = None
        except Exception as e:
            logger.exception(f"Move failed: {e}")
            status, score, error = "failed", 0.0, str(e)

        # `accepted` marks the steps that set a new best.
        improved = status == "ok" and score > campaign["best_score"]
        self.store.update_node(
            node_id, status=status, score=score, accepted=int(improved), error=error
        )
        logger.info(
            f"  → {status}, score {score:.3f} "
            f"(best so far {campaign['best_score']:.3f})"
            f"{', new best' if improved else ''}"
        )

        self._reflect(campaign_id, node_id, objective)
        self._advance(campaign, node_id, status, score, temperature)
        self._save_costs(campaign_id, run_id, node_id)
        return self.store.node(node_id)

    def _save_costs(
        self, campaign_id: int, run_id: int, node_id: Optional[int] = None
    ) -> None:
        """Persist LLM calls and Ouro route charges made since the last save."""
        self.store.add_llm_calls(campaign_id, run_id, node_id, self.llm.drain())
        if self.meter is not None:
            self.store.add_route_calls(
                campaign_id, run_id, node_id, self.meter.drain_charges()
            )

    def _advance(
        self,
        campaign: Dict[str, Any],
        node_id: int,
        status: str,
        score: float,
        temperature: float,
    ) -> None:
        """Count the step and move the search position to a new best."""
        updates: Dict[str, Any] = {
            "steps_done": campaign["steps_done"] + 1,
            "temperature": temperature,
        }
        if status == "ok" and score > campaign["best_score"]:
            updates.update(
                best_score=score,
                best_node_id=node_id,
                current_node_id=node_id,
                steps_since_best=0,
            )
        elif status == "ok":
            # A step that found or ran nothing says nothing about a stall.
            updates["steps_since_best"] = campaign["steps_since_best"] + 1
        self.store.update_campaign(campaign["id"], **updates)

    # -- propose / select ----------------------------------------

    def _research(
        self, record: str, state: str, campaign: Dict[str, Any]
    ) -> Optional[str]:
        """Bounded sub-agent: literature and memory lookups before proposing."""
        cfg = self.config
        if not cfg.research:
            return None
        tools = [query_memory_tool(self.store, campaign["id"])]
        if cfg.web_research:
            tools += [web_search_tool(self.llm, cfg.search_model), read_url_tool()]
        try:
            run = run_agent(
                self.llm,
                system=RESEARCH_SYSTEM,
                task=f"{state}\n\n## Your task\nPrepare the research brief "
                "for the next step.",
                tools=tools,
                result=ResearchBrief,
                purpose="research",
                shared=record,
                max_turns=cfg.research_max_turns,
                max_cost_usd=cfg.research_max_cost_usd,
            )
            return run.result.render()
        except Exception as e:
            logger.warning(f"Research step failed: {e}")
            return None

    def _propose(
        self,
        record: str,
        state: str,
        campaign: Dict[str, Any],
        objective: ObjectiveSpec,
        budget: Budget,
    ) -> List[Move]:
        prompt = (
            f"{state}\n\n## Your task\n"
            f"Propose {self.config.proposals_per_step} candidate next moves."
        )
        problems: List[str] = []
        for _ in range(2):
            ask = prompt
            if problems:
                ask += (
                    "\n\nYour previous proposals were all unusable:\n- "
                    + "\n- ".join(problems)
                )
            proposals = self.llm.structured(
                SEARCH_SYSTEM, ask, Proposals, shared=record, purpose="propose"
            )
            logger.info(f"Reading: {proposals.reading}")

            valid, problems = [], []
            for move in proposals.moves:
                problem = self._validate(move, campaign, objective, budget)
                if problem:
                    logger.warning(f"Dropping {move.kind} move: {problem}")
                    problems.append(f"{move.kind} {move.chemical_system}: {problem}")
                else:
                    valid.append(move)
            if valid:
                return valid
        raise RuntimeError(f"No valid moves proposed: {problems}")

    def _validate(
        self,
        move: Move,
        campaign: Dict[str, Any],
        objective: ObjectiveSpec,
        budget: Budget,
    ) -> Optional[str]:
        """Normalise a move in place; return why it is unusable, or None."""
        move.predicted_score = min(1.0, max(0.0, move.predicted_score))
        move.boldness = min(1.0, max(0.0, move.boldness))

        parent = self.store.node(move.from_node) if move.from_node else None
        if parent is None or parent["campaign_id"] != campaign["id"]:
            move.from_node = None
            parent = (
                self.store.node(campaign["current_node_id"])
                if campaign["current_node_id"]
                else None
            )

        if move.kind == "evaluate":
            wanted = move.properties or objective.properties()
            move.properties = [
                p for p in wanted if p in CATALOG and CATALOG[p].route
            ]
            if not move.properties:
                return "no measurable properties requested"
            if budget.expensive_left <= 0:
                move.properties = [
                    p for p in move.properties if CATALOG[p].tier != EXPENSIVE
                ]
                if not move.properties:
                    return "the expensive-calculation allowance for this run is spent"
            found = [self.store.candidate(c) for c in move.candidate_ids]
            found = [c for c in found if c and c["campaign_id"] == campaign["id"]]
            if not found:
                return "no known candidate ids given"
            found = [
                c
                for c in found
                if any(c["properties"].get(p) is None for p in move.properties)
            ]
            if not found:
                return "everything requested is already measured on those candidates"

            expensive = [p for p in move.properties if CATALOG[p].tier == EXPENSIVE]
            if expensive:
                # An expensive calculation must be able to change the answer.
                proven = self._proven_best(campaign["id"], objective)
                limit = min(
                    budget.expensive_left, self.config.max_expensive_evals_per_step
                )
                kept = []
                for c in found:
                    if any(c["properties"].get(p) is None for p in expensive):
                        if objective.ceiling(c["properties"]) <= proven or limit <= 0:
                            continue
                        limit -= 1
                    kept.append(c)
                found = kept
                if not found:
                    return (
                        "none of those candidates could beat the best fully "
                        f"measured score ({proven:.3f}) even with perfect "
                        "expensive results"
                    )
            move.candidate_ids = [c["id"] for c in found]
            return None

        if move.kind == "deepen" and not move.chemical_system and parent:
            move.chemical_system = parent["system"]
        if not move.chemical_system:
            return "no chemical system given"
        move.chemical_system = normalize_system(move.chemical_system)
        elements = parse_elements(move.chemical_system)
        if "-".join(elements) != move.chemical_system or not 1 <= len(elements) <= 5:
            return f"'{move.chemical_system}' is not 1-5 element symbols"
        problem = objective.check_system(elements)
        if problem:
            return problem

        move.crystal_systems = [
            c.lower() for c in move.crystal_systems if c.lower() in CRYSTAL_SYSTEM_NAMES
        ]
        move.fraction_bounds = [
            b for b in move.fraction_bounds if b.element in elements
        ]
        # Exploration cost grows steeply with cell size and trial count.
        if move.max_atoms:
            move.max_atoms = min(move.max_atoms, self.config.ggen_max_atoms_cap)
        if move.num_trials:
            move.num_trials = min(move.num_trials, self.config.ggen_num_trials_cap)
        earlier = self._covered_by(move, campaign)
        if earlier:
            return (
                f"{move.chemical_system} was already explored at node #{earlier} "
                "with constraints at least as wide; its result is in the record"
            )
        return None

    def _covered_by(self, move: Move, campaign: Dict[str, Any]) -> Optional[int]:
        """Id of an earlier exploration that already covers this move's reach."""
        cfg = self.config

        def reach(m: Dict[str, Any]):
            bounds = {
                b["element"]: (
                    b.get("min") or 0.0,
                    1.0 if b.get("max") is None else b["max"],
                )
                for b in m.get("fraction_bounds") or []
            }
            return (
                set(m.get("crystal_systems") or CRYSTAL_SYSTEM_NAMES),
                bounds,
                m.get("max_atoms") or cfg.ggen_max_atoms,
                m.get("num_trials") or cfg.ggen_num_trials,
            )

        systems, bounds, atoms, trials = reach(move.model_dump())
        for node in self.store.nodes(campaign["id"]):
            if (
                node["kind"] == "evaluate"
                or node["system"] != move.chemical_system
                or node["status"] not in ("ok", "empty")
            ):
                continue
            had_systems, had_bounds, had_atoms, had_trials = reach(node["move"])
            if (
                had_systems >= systems
                and had_atoms >= atoms
                and had_trials >= trials
                and all(
                    low <= bounds.get(el, (0.0, 1.0))[0]
                    and high >= bounds.get(el, (0.0, 1.0))[1]
                    for el, (low, high) in had_bounds.items()
                )
            ):
                return node["id"]
        return None

    def _select(self, moves: List[Move], temperature: float) -> Move:
        """Boltzmann selection over the LLM's own score predictions."""
        top = max(m.predicted_score for m in moves)
        weights = [math.exp((m.predicted_score - top) / temperature) for m in moves]
        return self.rng.choices(moves, weights=weights, k=1)[0]

    # -- execute ----------------------------------------------------------

    def _execute_explore(
        self,
        move: Move,
        node_id: int,
        campaign: Dict[str, Any],
        objective: ObjectiveSpec,
    ) -> Tuple[str, float]:
        exploration = self.explorer.explore(
            move.chemical_system,
            crystal_systems=move.crystal_systems,
            min_fraction={
                b.element: b.min for b in move.fraction_bounds if b.min is not None
            },
            max_fraction={
                b.element: b.max for b in move.fraction_bounds if b.max is not None
            },
            max_atoms=move.max_atoms,
            num_trials=move.num_trials,
            skip_existing=move.kind == "deepen",
        )
        self.store.update_node(
            node_id,
            system=exploration.chemical_system,
            summary=exploration.summary,
            assets=exploration.assets,
        )
        for cand in exploration.candidates:
            score, _ = objective.score(cand["properties"])
            self.store.add_candidate(
                campaign_id=campaign["id"],
                node_id=node_id,
                formula=cand["formula"],
                system=exploration.chemical_system,
                identity=cand.get("identity"),
                origin=cand.get("origin"),
                cif=cand["cif"],
                properties=cand["properties"],
                score=score,
            )

        candidates = self.store.candidates(campaign["id"], node_id)
        if not candidates:
            return "empty", 0.0

        # Stability values GGen didn't supply are measured alongside the cheap
        # tier. Expensive properties are left to `evaluate` moves, so they are
        # spent by choice across the whole campaign rather than on whatever a
        # step happens to find first.
        cheap = objective.properties(FREE) + objective.properties(CHEAP)
        picks = self._triage(
            objective,
            candidates,
            limit=self.config.max_cheap_evals_per_step,
            purpose=f"the cheap properties ({', '.join(cheap)})",
        )
        for cand in picks:
            self._measure(cand, cheap, objective)

        best = self.store.candidates(campaign["id"], node_id)[0]
        return "ok", best["score"]

    def _execute_evaluate(
        self,
        move: Move,
        node_id: int,
        campaign: Dict[str, Any],
        objective: ObjectiveSpec,
        budget: Budget,
    ) -> Tuple[str, float]:
        scores: List[float] = []
        owners = set()
        for candidate_id in move.candidate_ids:
            cand = self.store.candidate(candidate_id)
            properties = list(move.properties)
            needs_expensive = any(
                CATALOG[p].tier == EXPENSIVE and cand["properties"].get(p) is None
                for p in properties
            )
            if needs_expensive:
                if budget.expensive_left <= 0:
                    properties = [
                        p for p in properties if CATALOG[p].tier != EXPENSIVE
                    ]
                else:
                    budget.expensive_left -= 1
            measured = self._measure(cand, properties, objective)
            if needs_expensive and budget.expensive_left >= 0:
                budget.expensive_left += self._refund(measured, properties)
            scores.append(measured["score"])
            owners.add(cand["node_id"])

        # The nodes these candidates came from now have better information.
        for owner in owners:
            top = self.store.candidates(campaign["id"], owner)[0]
            self.store.update_node(owner, score=top["score"])

        first = self.store.candidate(move.candidate_ids[0])
        self.store.update_node(
            node_id,
            system=first["system"],
            summary={"evaluated_candidates": move.candidate_ids},
        )
        return "ok", max(scores)

    @staticmethod
    def _refund(candidate: Dict[str, Any], properties: List[str]) -> int:
        """1 when every expensive calculation errored: nothing was learned, so
        the run's allowance should not be charged for it."""
        expensive = [p for p in properties if CATALOG[p].tier == EXPENSIVE]
        errored = [p for p in expensive if p in candidate["errors"]]
        return 1 if expensive and len(errored) == len(expensive) else 0

    def _measure(
        self, candidate: Dict[str, Any], properties: List[str], objective: ObjectiveSpec
    ) -> Dict[str, Any]:
        measurement = self.evaluator.measure(candidate, properties)
        merged = {**candidate["properties"], **measurement.values}
        score, _ = objective.score(merged)
        self.store.update_candidate(
            candidate["id"],
            file_id=measurement.file_id,
            properties=merged,
            errors={
                k: v
                for k, v in {**candidate["errors"], **measurement.errors}.items()
                if k not in measurement.values
            },
            artifacts={**candidate["artifacts"], **measurement.artifacts},
            score=score,
            evaluated=1,
        )
        return self.store.candidate(candidate["id"])

    def _triage(
        self,
        objective: ObjectiveSpec,
        candidates: List[Dict[str, Any]],
        limit: int,
        purpose: str,
        fallback_all: bool = True,
    ) -> List[Dict[str, Any]]:
        """LLM picks up to ``limit`` candidates worth measuring."""
        if limit <= 0 or not candidates:
            return []
        # One entry per distinct structure, so a slot is never spent twice
        # on the same crystal written in different cell sizes.
        candidates = representatives(candidates)
        by_id = {c["id"]: c for c in candidates}
        prompt = (
            f"## Objective\n{objective.describe()}\n\n"
            f"## Candidates\n{_candidate_table(candidates)}\n\n"
            f"## Your task\nSelect up to {limit} candidates to measure for {purpose}."
        )
        try:
            triage = self.llm.structured(
                TRIAGE_SYSTEM, prompt, Triage, purpose="triage"
            )
        except Exception as e:
            logger.warning(f"Triage failed ({e}); falling back to top scores")
            return candidates[:limit] if fallback_all else []

        picks: List[Dict[str, Any]] = []
        for selection in triage.selections:
            cand = by_id.get(selection.candidate_id)
            if cand and cand not in picks:
                logger.info(f"  triage: {cand['formula']} — {selection.reason}")
                picks.append(cand)
        return picks[:limit]

    # -- reflect ----------------------------------------------------------

    def _reflect(self, campaign_id: int, node_id: int, objective: ObjectiveSpec) -> None:
        node = self.store.node(node_id)
        move = node["move"]
        if node["kind"] == "evaluate":
            candidates = [
                self.store.candidate(c) for c in move.get("candidate_ids", [])
            ]
        else:
            candidates = self.store.candidates(campaign_id, node_id)

        prompt = (
            f"## Objective\n{objective.describe()}\n\n"
            f"## Move\n{_describe_move(node)}\n"
            f"Rationale: {move.get('rationale')}\n"
            f"Prediction: {move.get('prediction')}\n"
            f"Predicted best score: {move.get('predicted_score')}\n\n"
            f"## Result\nStatus: {node['status']}"
            f"{' — ' + node['error'] if node['error'] else ''}\n"
            f"Best score: {node['score']:.3f}\n"
            f"Exploration summary: {json.dumps(node['summary'])}\n"
            f"Candidates:\n{_candidate_table(candidates[:25])}"
        )
        try:
            reflection = self.llm.structured(
                REFLECT_SYSTEM, prompt, Reflection, purpose="reflect"
            )
        except Exception as e:
            logger.warning(f"Reflection failed: {e}")
            return
        self.store.update_node(
            node_id,
            outcome=reflection.outcome,
            surprise=min(1.0, max(0.0, reflection.surprise)),
        )
        for text in reflection.learnings[:3]:
            logger.info(f"  learned: {text}")
            self.store.add_learning(campaign_id, node_id, text)

    # -- context ----------------------------------------------------------

    def _state(
        self,
        campaign: Dict[str, Any],
        objective: ObjectiveSpec,
        index: int,
        budget: Budget,
        temperature: float,
        verdict: Verdict,
    ) -> str:
        """What changes every step: the budget, the schedule, and the gap."""
        cfg = self.config
        lines = [
            "## Search state",
            f"Step {index + 1} of {cfg.max_steps} in this run; "
            f"{campaign['steps_done']} steps taken over "
            f"{len(self.store.runs(campaign['id']))} runs so far.",
            f"Temperature {temperature:.3f} on the 0-1 score scale "
            f"(falls from {cfg.t_initial} to {cfg.t_min} over the run).",
            f"Best score so far: {campaign['best_score']:.3f}.",
            f"Expensive calculations left in this run: {budget.expensive_left}.",
        ]
        if objective.properties(EXPENSIVE):
            cap = objective.reachable(
                objective.properties(FREE) + objective.properties(CHEAP)
            )
            lines.append(
                "An explore or deepen move measures only the free and cheap "
                f"properties, so its score cannot exceed {cap:.2f}; only an "
                "evaluate move that adds the expensive ones can score higher."
            )
        return "\n".join(lines) + f"\n\n## Where the goal stands\n{verdict.reason}"

    def _record(self, campaign: Dict[str, Any], objective: ObjectiveSpec) -> str:
        """The search record. Identical for every call within a step, so it is
        sent as the shared (cached) prefix."""
        cid = campaign["id"]
        nodes = self.store.nodes(cid)
        path = self.store.path_to(campaign["current_node_id"])
        path_ids = {n["id"] for n in path}
        candidates = self.store.candidates(cid)

        parts = [
            f"## Objective\n{objective.describe()}",
            f"## Measurable properties\n{describe_catalog()}",
        ]
        if self.config.world_note:
            parts.append(f"## About this world\n{self.config.world_note}")

        if not nodes:
            parts.append(
                "## Path\nNothing has been explored yet. This is the first step: "
                "choose starting points from your knowledge of the field."
            )
        else:
            parts.append(
                "## Path to the best position so far (root first)\n"
                + "\n".join(_node_line(n, detail=True) for n in path)
            )
            off_path = [n for n in nodes if n["id"] not in path_ids]
            if off_path:
                parts.append(
                    "## Other branches tried\n"
                    + "\n".join(_node_line(n, detail=False) for n in off_path)
                )

        candidates = annotate_equivalents(candidates)
        for c in candidates:
            if c["evaluated"]:
                c["ceiling"] = objective.ceiling(c["properties"])
        evaluated = [c for c in candidates if c["evaluated"]]
        if evaluated:
            parts.append(
                "## Best measured candidates\n" + _candidate_table(evaluated[:20])
            )
        # A structure counts as measured once any of its equivalent cells is.
        measured_ids = {c["id"] for c in evaluated}
        unevaluated = sorted(
            (
                c
                for c in representatives(candidates)
                if not c["evaluated"]
                and not measured_ids & set(c.get("equivalents", []))
            ),
            key=lambda c: c["properties"].get("e_hull", float("inf")),
        )
        if unevaluated:
            parts.append(
                "## Found but not yet measured (available to `evaluate` moves)\n"
                + _candidate_table(unevaluated[:25])
            )

        learnings = self.store.learnings(cid)
        if learnings:
            parts.append(
                "## Learnings so far\n"
                + "\n".join(f"- (node #{l['node_id']}) {l['text']}" for l in learnings)
            )
        elsewhere = self.store.other_campaign_learnings(cid)
        if elsewhere:
            parts.append(
                "## Learnings from campaigns with other goals\n"
                + "\n".join(f"- {l['text']} [goal: {l['goal'][:80]}]" for l in elsewhere)
            )

        notes = [n for n in nodes if n.get("research")][-2:]
        if notes:
            parts.append(
                "## Earlier research briefs\n"
                + "\n\n".join(f"(before node #{n['id']})\n{n['research']}" for n in notes)
            )
        return "\n\n".join(parts)


# -- formatting ---------------------------------------------------------------


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.3g}"
    return str(value)


def _candidate_table(candidates: List[Dict[str, Any]]) -> str:
    lines = []
    for c in candidates:
        if c is None:
            continue
        props = dict(c["properties"])
        sg = props.pop("space_group", None)
        measured = ", ".join(f"{k}={_fmt(v)}" for k, v in props.items())
        # A route error means "unknown", never a negative result.
        failed = (
            " | NOT MEASURED (calculation errored, value unknown): "
            + ", ".join(c["errors"])
            if c["errors"]
            else ""
        )
        # Only worth stating when something is still unmeasured.
        potential = (
            f" (up to {c['ceiling']:.3f} once fully measured)"
            if c.get("ceiling", 0) > c["score"] + 5e-4
            else ""
        )
        same = (
            f" | same structure as candidates {c['equivalents']} "
            "(other cell sizes)"
            if c.get("equivalents")
            else ""
        )
        lines.append(
            f"- candidate {c['id']}: {c['formula']} "
            f"(space group {sg}, {crystal_system(sg)}; node #{c['node_id']}"
            f"{'; ' + c['origin'] if c.get('origin') else ''}) | "
            f"{measured or 'nothing measured'} | score {c['score']:.3f}"
            f"{potential}{failed}{same}"
        )
    return "\n".join(lines) or "(none)"


def _identity(candidate: Dict[str, Any]) -> str:
    return candidate.get("identity") or identity_key(
        candidate["formula"], candidate["properties"]
    )


def annotate_equivalents(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Give each candidate the ids of the others that are the same structure."""
    groups: Dict[str, List[int]] = {}
    for c in candidates:
        groups.setdefault(_identity(c), []).append(c["id"])
    return [
        {**c, "equivalents": [i for i in groups[_identity(c)] if i != c["id"]]}
        for c in candidates
    ]


def representatives(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One candidate per distinct structure, in the original order.

    Prefers a member that has already been measured, then the smallest cell,
    since calculations on it are cheapest.
    """
    best: Dict[str, Dict[str, Any]] = {}
    for c in annotate_equivalents(candidates):
        key = _identity(c)
        rank = (
            not c["evaluated"],
            c["properties"].get("num_atoms") or float("inf"),
            c["id"],
        )
        if key not in best or rank < best[key][0]:
            best[key] = (rank, c)
    chosen = {c["id"] for _, c in best.values()}
    return [c for c in annotate_equivalents(candidates) if c["id"] in chosen]


def _describe_move(node: Dict[str, Any]) -> str:
    move = node["move"]
    if node["kind"] == "evaluate":
        return (
            f"evaluate candidates {move.get('candidate_ids')} "
            f"for {', '.join(move.get('properties') or [])}"
        )
    constraints = []
    if move.get("crystal_systems"):
        constraints.append("/".join(move["crystal_systems"]))
    for b in move.get("fraction_bounds") or []:
        constraints.append(f"{b['element']} in [{b.get('min')}, {b.get('max')}]")
    if move.get("max_atoms"):
        constraints.append(f"max_atoms={move['max_atoms']}")
    if move.get("num_trials"):
        constraints.append(f"num_trials={move['num_trials']}")
    suffix = f" ({'; '.join(constraints)})" if constraints else ""
    return f"{node['kind']} {node['system']}{suffix}"


def _node_line(node: Dict[str, Any], detail: bool) -> str:
    move = node["move"]
    line = (
        f"- node #{node['id']} (run {node['run_id']}, from "
        f"#{node['parent_id']}): {_describe_move(node)} → {node['status']}, "
        f"score {node['score']:.3f} vs predicted "
        f"{move.get('predicted_score', 0):.2f}"
        f"{', new best' if node['accepted'] else ''}"
    )
    if node.get("error"):
        line += f"\n  error: {node['error'][:300]}"
    if detail and move.get("rationale"):
        line += f"\n  why: {move['rationale']}"
    if node.get("outcome"):
        line += f"\n  outcome: {node['outcome']}"
    return line
