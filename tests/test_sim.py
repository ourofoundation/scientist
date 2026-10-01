"""The simulated world: deterministic, and usable in place of GGen and routes."""

import random

from scientist.computational.sim import SimEvaluator, SimExplorer, SimWorld
from scientist.core.config import ScientistConfig
from scientist.core.search import Search
from scientist.core.store import Store

from test_search import OBJECTIVE, FakeLLM, move


def test_world_is_deterministic_per_seed():
    a, b, other = SimWorld(0), SimWorld(0), SimWorld(1)
    assert a.phases("Co-Fe-W") == b.phases("Co-Fe-W")
    assert a.phases("Co-Fe-W") != other.phases("Co-Fe-W")
    phase = a.phases("Fe-Pt")[0]
    args = ("magnetic_anisotropy_energy", phase.formula, phase.space_group, 0.0)
    assert a.value(*args) == b.value(*args)


def test_explore_respects_constraints_and_trials():
    explorer = SimExplorer(SimWorld(0))
    full = explorer.explore("Fe-Pt", num_trials=20)
    assert full.candidates and full.summary["stoichiometries_tried"] > 0
    assert len(explorer.explore("pt, fe", num_trials=1).candidates) < len(
        full.candidates
    )
    rich = explorer.explore("Fe-Pt", num_trials=20, min_fraction={"Fe": 0.6})
    assert 0 < len(rich.candidates) < len(full.candidates)
    small = explorer.explore("Fe-Pt", num_trials=20, max_atoms=3)
    assert all(c["properties"]["num_atoms"] <= 3 for c in small.candidates)


def test_failures_are_errors_and_charges_are_metered():
    world = SimWorld(0, failure_rate=1.0)
    candidate = SimExplorer(world).explore("Fe-Pt").candidates[0]
    measured = SimEvaluator(world).measure(candidate, ["cost", "curie_temperature"])
    assert not measured.values and set(measured.errors) == {
        "cost",
        "curie_temperature",
    }
    assert [c["route"] for c in world.drain_charges()] == [
        "sim/explore",
        "sim/cost",
        "sim/curie_temperature",
    ]
    assert world.drain_charges() == []


def test_search_runs_against_the_simulated_world():
    world = SimWorld(0)
    store = Store(":memory:")
    config = ScientistConfig(research=False, db_path=":memory:", max_steps=1)
    search = Search(
        config,
        FakeLLM([[move(system="Fe-Pt")]]),
        store,
        SimExplorer(world),
        SimEvaluator(world),
        rng=random.Random(0),
        meter=world,
    )
    campaign = search.open_campaign("find a magnet")
    result = search.run(campaign["id"], store.start_run(campaign["id"]))

    assert result["nodes"][0]["status"] == "ok"
    assert any(
        "curie_temperature" in c["properties"]
        for c in store.candidates(campaign["id"])
    )
    assert result["usage"]["routes"]["calls"] > 1

    top = world.oracle(OBJECTIVE, max_elements=1)
    assert top == sorted(top, key=lambda e: -e["score"])
