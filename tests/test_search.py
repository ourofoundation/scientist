"""Search loop tests against fake LLM, GGen and route backends."""

import random
import re

import pytest

from scientist.computational.evaluator import Measurement
from scientist.computational.explorer import (
    Exploration,
    identity_key,
    reduced_formula,
)
from scientist.core.config import ScientistConfig
from scientist.core.llm import extract_json, strict_schema
from scientist.core.objective import Criterion, ObjectiveSpec
from scientist.core.search import (
    Budget,
    Move,
    Proposals,
    Reflection,
    Search,
    Selection,
    Triage,
    campaign_key,
    representatives,
)
from scientist.core.store import Store
from scientist.utils.report import build_inventory, parse_reply, sanitize

OBJECTIVE = ObjectiveSpec(
    title="RE-free magnet",
    summary="Rare-earth-free magnet with high Tc and MAE.",
    required_elements=[],
    forbidden_elements=["Nd", "Gd"],
    criteria=[
        Criterion(
            property="e_hull", direction="minimize", target=0.1, weight=1, hard=False
        ),
        Criterion(
            property="curie_temperature",
            direction="maximize",
            target=500,
            weight=1,
            hard=False,
        ),
        Criterion(
            property="magnetic_anisotropy_energy",
            direction="maximize",
            target=1.0,
            weight=1,
            hard=False,
        ),
    ],
    unsupported=[],
    notes="",
)

# formula → (e_hull, Tc, MAE)
WORLD = {
    "Co-Fe": {"FeCo": (0.0, 900.0, 0.1), "Fe3Co": (0.02, 800.0, 0.05)},
    "Fe-Ni": {"FeNi": (0.01, 700.0, 1.2)},
    "Al-Cu": {"AlCu": (0.0, 5.0, 0.0)},
}


def move(kind="explore", system=None, predicted=0.5, **kw):
    base = dict(
        kind=kind,
        from_node=None,
        chemical_system=system,
        crystal_systems=[],
        fraction_bounds=[],
        max_atoms=None,
        num_trials=None,
        candidate_ids=[],
        properties=[],
        boldness=0.5,
        predicted_score=predicted,
        prediction="p",
        rationale="r",
    )
    base.update(kw)
    return Move(**base)


class FakeLLM:
    model = "fake"

    def __init__(self, proposals):
        self.proposals = list(proposals)
        self.prompts = []
        self.calls = []

    def drain(self):
        calls, self.calls = self.calls, []
        return calls

    def text(self, system, user, **kwargs):
        return "notes"

    def structured(self, system, user, schema, shared=None, purpose=None):
        self.calls.append(
            {"purpose": purpose or schema.__name__, "model": "fake", "cost_usd": 0.01}
        )
        self.prompts.append((schema.__name__, f"{shared or ''}\n{user}"))
        if schema is ObjectiveSpec:
            return OBJECTIVE.model_copy(deep=True)
        if schema is Proposals:
            return Proposals(reading="reading", moves=self.proposals.pop(0))
        if schema is Triage:
            listing = user.split("## Candidates")[1]
            ids = [int(i) for i in re.findall(r"candidate (\d+):", listing)]
            return Triage(
                selections=[Selection(candidate_id=i, reason="x") for i in ids],
                notes="",
            )
        if schema is Reflection:
            return Reflection(
                outcome="it ran", surprise=0.2, learnings=["LEARNED-SOMETHING"]
            )
        raise AssertionError(schema)


class FakeExplorer:
    def __init__(self):
        self.calls = []

    def explore(self, system, **kwargs):
        self.calls.append((system, kwargs))
        if system == "Bi-Mn":
            raise RuntimeError("GGen exploded")
        return Exploration(
            chemical_system=system,
            candidates=[
                {
                    "formula": formula,
                    "cif": "data_x",
                    "properties": {"e_hull": values[0], "space_group": 123},
                    "identity": identity_key(
                        formula, {"e_hull": values[0], "space_group": 123}
                    ),
                }
                for formula, values in WORLD.get(system, {}).items()
            ],
            summary={"near_hull": len(WORLD.get(system, {}))},
            assets={"phase_diagram": "11111111-1111-1111-1111-111111111111"},
        )


class FakeEvaluator:
    def __init__(self):
        self.calls = []

    def measure(self, candidate, properties):
        self.calls.append((candidate["formula"], list(properties)))
        _, tc, mae = WORLD[candidate["system"]][candidate["formula"]]
        table = {"curie_temperature": tc, "magnetic_anisotropy_energy": mae}
        return Measurement(
            file_id="22222222-2222-2222-2222-222222222222",
            values={
                p: table[p]
                for p in properties
                if p in table and candidate["properties"].get(p) is None
            },
        )


def make_search(store, proposals, **config):
    cfg = ScientistConfig(research=False, db_path=":memory:", **config)
    llm = FakeLLM(proposals)
    search = Search(
        cfg, llm, store, FakeExplorer(), FakeEvaluator(), rng=random.Random(0)
    )
    return search, llm


def start(search, store):
    campaign = search.open_campaign("find a magnet")
    return campaign["id"], store.start_run(campaign["id"])


def test_objective_scoring():
    full, _ = OBJECTIVE.score(
        {"e_hull": 0.0, "curie_temperature": 5000, "magnetic_anisotropy_energy": 50}
    )
    assert full > 0.99
    partial, breakdown = OBJECTIVE.score({"e_hull": 0.0, "curie_temperature": 500})
    assert breakdown["magnetic_anisotropy_energy"] == 0
    assert partial == pytest.approx((1 + 0.6321) / 3, abs=1e-3)

    hard = OBJECTIVE.model_copy(deep=True)
    hard.criteria[1].hard = True
    violated, _ = hard.score({"e_hull": 0.0, "curie_temperature": 100})
    soft, _ = OBJECTIVE.score({"e_hull": 0.0, "curie_temperature": 100})
    assert violated == pytest.approx(soft / 2)

    assert OBJECTIVE.check_system(["Fe", "Nd"]) is not None
    assert OBJECTIVE.check_system(["Fe", "Co"]) is None


def evaluate(ids, properties=("magnetic_anisotropy_energy",), **kw):
    return move(kind="evaluate", candidate_ids=list(ids), properties=list(properties), **kw)


def test_run_records_path_and_cools():
    store = Store(":memory:")
    search, llm = make_search(
        store,
        [[move(system="Fe-Co", predicted=0.6)], [move(system="Al-Cu", predicted=0.7)]],
        max_steps=2,
    )
    cid, run_id = start(search, store)
    result = search.run(cid, run_id)

    nodes = result["nodes"]
    assert [n["system"] for n in nodes] == ["Co-Fe", "Al-Cu"]
    assert nodes[1]["parent_id"] == nodes[0]["id"]
    assert all(n["status"] == "ok" and n["outcome"] == "it ran" for n in nodes)
    assert [n["accepted"] for n in nodes] == [1, 0]

    # The search stays at the best node, and exploring spends nothing expensive.
    campaign = result["campaign"]
    assert campaign["current_node_id"] == campaign["best_node_id"] == nodes[0]["id"]
    assert not any(
        "magnetic_anisotropy_energy" in c["properties"] for c in store.candidates(cid)
    )
    # Temperature falls from t_initial to t_min across the run's steps.
    assert [n["temperature"] for n in nodes] == pytest.approx([0.25, 0.02])
    assert result["verdict"]["status"] == "not_yet"
    assert len(store.learnings(cid)) == 2
    assert store.candidate(1)["file_id"]


def test_run_stops_when_the_goal_is_met():
    store = Store(":memory:")
    search, llm = make_search(
        store,
        [[move(system="Fe-Ni")], [evaluate([1])], [move(system="Fe-Co")]],
        max_steps=3,
    )
    cid, run_id = start(search, store)
    result = search.run(cid, run_id)

    assert [n["kind"] for n in result["nodes"]] == ["explore", "evaluate"]
    assert result["verdict"]["status"] == "met"
    assert result["best_candidate"]["formula"] == "FeNi"
    # Before the evaluate move, the model was told what was missing.
    gap = [p for name, p in llm.prompts if name == "Proposals"][1]
    assert "up to" in gap and "magnetic_anisotropy_energy is measured" in gap


def test_run_stops_when_blocked_on_expensive_budget():
    store = Store(":memory:")
    search, _ = make_search(
        store,
        [[move(system="Fe-Co")], [evaluate([1, 2])], [move(system="Fe-Ni")]],
        max_steps=4,
        max_expensive_evals=1,
    )
    cid, run_id = start(search, store)
    result = search.run(cid, run_id)

    expensive = [
        c for c in search.evaluator.calls if "magnetic_anisotropy_energy" in c[1]
    ]
    assert len(expensive) == 1
    assert len(result["nodes"]) == 2
    assert result["verdict"]["status"] == "blocked"
    assert result["best_candidate"]["formula"] == "FeCo"  # the fully measured one


def test_expensive_calculation_must_be_able_to_change_the_answer():
    WORLD["Fe-Pd"] = {"FePd": (0.01, 700.0, 0.9)}
    try:
        store = Store(":memory:")
        search, llm = make_search(
            store,
            [
                [move(system="Fe-Pd")],
                [evaluate([1])],
                [move(system="Al-Cu")],
                [evaluate([2])],
            ],
            max_steps=4,
        )
        cid, run_id = start(search, store)
        nodes = search.run(cid, run_id)["nodes"]
    finally:
        del WORLD["Fe-Pd"]
    # AlCu cannot beat FePd even with a perfect result, so it is never run.
    assert len(nodes) == 3
    assert ("AlCu", ["magnetic_anisotropy_energy"]) not in search.evaluator.calls
    assert "could beat the best fully" in llm.prompts[-1][1]


def test_repeated_exploration_is_refused():
    store = Store(":memory:")
    search, llm = make_search(
        store,
        [
            [move(system="Fe-Co")],
            [move(system="Fe-Co", max_atoms=8)],
            [move(kind="deepen", system="Fe-Co", num_trials=20)],
        ],
        max_steps=2,
    )
    cid, run_id = start(search, store)
    search.run(cid, run_id)
    assert [kw["num_trials"] for _, kw in search.explorer.calls] == [None, 20]
    reprompt = [p for name, p in llm.prompts if name == "Proposals"][-1]
    assert "already explored at node #1" in reprompt


def test_second_run_sees_history():
    store = Store(":memory:")
    search, _ = make_search(store, [[move(system="Fe-Co")]], max_steps=1)
    cid, run_id = start(search, store)
    search.run(cid, run_id)

    search2, llm2 = make_search(store, [[move(system="Fe-Ni")]], max_steps=1)
    campaign = search2.open_campaign("Find a  MAGNET")  # same goal, same campaign
    assert campaign["id"] == cid
    search2.run(cid, store.start_run(cid))

    assert not any(name == "ObjectiveSpec" for name, _ in llm2.prompts)
    propose = next(p for name, p in llm2.prompts if name == "Proposals")
    assert "explore Co-Fe" in propose
    assert "LEARNED-SOMETHING" in propose
    assert "FeCo" in propose
    assert store.campaign(cid)["steps_done"] == 2


def test_invalid_moves_are_dropped_and_reprompted():
    store = Store(":memory:")
    search, llm = make_search(
        store,
        [[move(system="Fe-Nd")], [move(system="Fe-Nd"), move(system="fe, co")]],
        max_steps=1,
    )
    cid, run_id = start(search, store)
    result = search.run(cid, run_id)
    assert [n["system"] for n in result["nodes"]] == ["Co-Fe"]
    reprompt = [p for name, p in llm.prompts if name == "Proposals"][1]
    assert "forbidden" in reprompt


def test_failed_move_is_recorded_not_accepted():
    store = Store(":memory:")
    search, _ = make_search(
        store, [[move(system="Fe-Co")], [move(system="Mn-Bi")]], max_steps=2
    )
    cid, run_id = start(search, store)
    nodes = search.run(cid, run_id)["nodes"]
    assert nodes[1]["status"] == "failed"
    assert "GGen exploded" in nodes[1]["error"]
    assert not nodes[1]["accepted"]
    assert store.campaign(cid)["current_node_id"] == nodes[0]["id"]


def test_evaluate_move_updates_owner_node():
    store = Store(":memory:")
    search, _ = make_search(
        store,
        [
            [move(system="Fe-Ni")],
            [
                evaluate([1])
            ],
        ],
        max_steps=2,
    )
    cid, run_id = start(search, store)
    nodes = search.run(cid, run_id)["nodes"]
    before, _ = OBJECTIVE.score({"e_hull": 0.01, "curie_temperature": 700.0})
    assert store.candidate(1)["properties"]["magnetic_anisotropy_energy"] == 1.2
    assert nodes[1]["kind"] == "evaluate" and nodes[1]["score"] > before
    assert store.node(nodes[0]["id"])["score"] == nodes[1]["score"]
    # The exploring step keeps the score it reached on its own.
    assert store.node(nodes[0]["id"])["first_score"] == pytest.approx(before)


def test_selection_follows_temperature():
    store = Store(":memory:")
    search, _ = make_search(store, [])
    moves = [move(system="Fe-Co", predicted=0.2), move(system="Fe-Ni", predicted=0.8)]

    cold = [search._select(moves, 0.01).chemical_system for _ in range(50)]
    assert set(cold) == {"Fe-Ni"}
    hot = [search._select(moves, 5.0).chemical_system for _ in range(200)]
    assert 40 < hot.count("Fe-Co") < 160


def test_stall_reheats_and_failures_do_not_count():
    store = Store(":memory:")
    search, _ = make_search(
        store,
        [
            [move(system="Fe-Co")],
            [move(system="Mn-Bi")],  # fails
            [move(system="Al-Cu")],
            [move(system="Fe-Ni")],
        ],
        max_steps=4,
        stall_steps=1,
    )
    cid, run_id = start(search, store)
    nodes = search.run(cid, run_id)["nodes"]
    campaign = store.campaign(cid)
    assert campaign["current_node_id"] == nodes[0]["id"]
    assert campaign["steps_since_best"] == 2
    schedule = [0.25 * (0.02 / 0.25) ** (i / 3) for i in range(4)]
    # Only the step after a productive step without a new best is reheated.
    assert [n["temperature"] for n in nodes] == pytest.approx(
        [schedule[0], schedule[1], schedule[2], 2 * schedule[3]]
    )


def test_campaign_key_is_stable():
    assert campaign_key("Find a  magnet\n") == campaign_key("find a magnet")
    assert campaign_key("find a magnet") != campaign_key("find a battery")


def test_strict_schema_and_json_extraction():
    schema = strict_schema(Proposals)
    move_schema = schema["$defs"]["Move"]
    assert move_schema["additionalProperties"] is False
    assert set(move_schema["required"]) == set(move_schema["properties"])
    assert extract_json('```json\n{"a": {"b": 1}}\n```') == {"a": {"b": 1}}


def test_report_sanitize_and_inventory():
    good = "11111111-1111-1111-1111-111111111111"
    bad = "99999999-9999-9999-9999-999999999999"
    body = (
        f'```assetComponent\n{{"id": "{good}", "assetType": "file"}}\n```\n'
        f'```assetComponent\n{{"id": "{bad}", "assetType": "file"}}\n```\n'
        f"see [structure](file:{good}) and [ghost](file:{bad})"
    )
    cleaned = sanitize(body, {good})
    assert good in cleaned and bad not in cleaned
    assert "ghost" in cleaned and "[ghost]" not in cleaned

    title, description, text = parse_reply("TITLE: A\nDESCRIPTION: B\n---\nbody")
    assert (title, description, text) == ("A", "B", "body")

    store = Store(":memory:")
    search, _ = make_search(store, [[move(system="Fe-Ni")]], max_steps=1)
    cid, run_id = start(search, store)
    result = search.run(cid, run_id)
    inventory = build_inventory(result["nodes"], store.candidates(cid))
    kinds = {(i["type"], i["id"]) for i in inventory}
    assert ("file", good) in kinds
    assert ("file", "22222222-2222-2222-2222-222222222222") in kinds


def test_strict_schema_keeps_fields_named_like_keywords():
    schema = strict_schema(ObjectiveSpec)
    assert "title" in schema["properties"] and "title" in schema["required"]
    assert "title" not in schema["properties"]["title"]


def test_equivalent_cells_are_grouped():
    assert reduced_formula("B4Fe8") == reduced_formula("Fe2B") == "BFe2"
    assert reduced_formula("Fe5B2P") == "B2Fe5P"

    def cand(i, formula, atoms, e_hull, evaluated=0, sg=140):
        props = {"e_hull": e_hull, "space_group": sg, "num_atoms": atoms}
        return {
            "id": i,
            "formula": formula,
            "identity": identity_key(formula, props),
            "properties": props,
            "evaluated": evaluated,
        }

    cands = [
        cand(1, "B2Fe4", 12, 0.0),
        cand(2, "Fe2B", 6, 0.0001),
        cand(3, "B4Fe8", 12, -0.0),
        cand(4, "Fe5B2P", 16, 0.0),
        cand(5, "Fe2B", 6, 0.05),  # same formula and group, different energy
    ]
    reps = representatives(cands)
    assert [c["id"] for c in reps] == [2, 4, 5]  # smallest Fe2B cell wins
    assert reps[0]["equivalents"] == [1, 3]

    cands[0]["evaluated"] = 1
    assert [c["id"] for c in representatives(cands)] == [1, 4, 5]


def test_triage_sees_one_entry_per_structure():
    WORLD["B-Fe"] = {"Fe2B": (0.0, 700.0, 0.1), "B2Fe4": (0.0, 700.0, 0.1)}
    try:
        store = Store(":memory:")
        search, llm = make_search(store, [[move(system="B-Fe")]], max_steps=1)
        cid, run_id = start(search, store)
        search.run(cid, run_id)
    finally:
        del WORLD["B-Fe"]
    cheap = [c for c in search.evaluator.calls if "curie_temperature" in c[1]]
    assert len(cheap) == 1
    assert len(store.candidates(cid)) == 2
