"""Live events and researcher direction."""

from scientist.core.store import Store

from test_search import make_search, move, start


def prompts(llm, name="Proposals"):
    return [p for n, p in llm.prompts if n == name]


def test_run_emits_events_in_order():
    store = Store(":memory:")
    search, _ = make_search(
        store, [[move(system="Co-Fe", predicted=0.6), move(system="Fe-Ni")]], max_steps=1
    )
    cid, run_id = start(search, store)
    search.run(cid, run_id)

    events = store.events(cid)
    kinds = [e["kind"] for e in events]
    for earlier, later in zip(
        ["run_started", "step_started", "proposed", "selected", "exploring",
         "explored", "triaged", "measured", "step_done", "reflected", "run_finished"],
        ["step_started", "proposed", "selected", "exploring", "explored",
         "triaged", "measured", "step_done", "reflected", "run_finished"],
    ):
        assert kinds.index(earlier) < kinds.index(later), (earlier, later)

    proposed = next(e for e in events if e["kind"] == "proposed")["data"]
    assert len(proposed["moves"]) == 2
    assert abs(sum(m["probability"] for m in proposed["moves"]) - 1) < 0.01
    selected = next(e for e in events if e["kind"] == "selected")
    assert selected["data"]["by"] == "temperature" and selected["node_id"]
    assert store.events(cid, after=events[-1]["id"]) == []


def test_steer_note_reaches_the_proposal_and_the_record():
    store = Store(":memory:")
    search, llm = make_search(
        store, [[move(system="Co-Fe")], [move(system="Fe-Ni")]], max_steps=2
    )
    cid, run_id = start(search, store)
    once = store.add_directive(cid, "steer", {"text": "ONE-SHOT-NOTE"})
    sticky = store.add_directive(cid, "steer", {"text": "STICKY-NOTE"}, sticky=True)
    nodes = search.run(cid, run_id)["nodes"]

    first, second = prompts(llm)
    assert "Direction from the researcher" in first
    assert "ONE-SHOT-NOTE" in first and "STICKY-NOTE" in first
    # The one-shot note is spent; the record still says it was given.
    assert "STICKY-NOTE" in second.split("## Direction from the researcher")[1]
    assert "ONE-SHOT-NOTE" not in second.split("## Direction from the researcher")[1]
    assert "researcher direction at this step: ONE-SHOT-NOTE" in second

    by_id = {d["id"]: d for d in store.directives(cid, status=None)}
    assert by_id[once]["status"] == "applied"
    assert by_id[once]["node_id"] == nodes[0]["id"]
    assert by_id[sticky]["status"] == "pending"
    assert [d["id"] for d in nodes[0]["move"]["directives"]] == [once, sticky]


def test_queued_move_runs_instead_of_a_proposal():
    store = Store(":memory:")
    search, llm = make_search(store, [[move(system="Co-Fe")]], max_steps=2)
    cid, run_id = start(search, store)
    store.add_directive(cid, "queue", {"kind": "explore", "chemical_system": "fe, ni"})
    nodes = search.run(cid, run_id)["nodes"]

    assert [n["system"] for n in nodes] == ["Fe-Ni", "Co-Fe"]
    assert nodes[0]["move"]["selected_by"] == "researcher"
    assert nodes[1]["move"]["selected_by"] == "temperature"
    assert len(prompts(llm)) == 1  # the queued step asked the model for nothing


def test_queued_move_is_held_to_validation():
    store = Store(":memory:")
    search, _ = make_search(store, [[move(system="Co-Fe")]], max_steps=1)
    cid, run_id = start(search, store)
    bad = store.add_directive(cid, "queue", {"kind": "explore", "chemical_system": "Fe-Nd"})
    nodes = search.run(cid, run_id)["nodes"]

    assert [n["system"] for n in nodes] == ["Co-Fe"]
    directive = store.directives(cid, status="rejected")[0]
    assert directive["id"] == bad and "forbidden" in directive["note"]
    assert "directive_rejected" in [e["kind"] for e in store.events(cid)]


def test_pause_lets_the_researcher_choose():
    store = Store(":memory:")
    proposals = [[move(system="Co-Fe", predicted=0.9), move(system="Fe-Ni", predicted=0.1)]]
    search, _ = make_search(store, proposals, max_steps=1, pause_poll_seconds=0)
    cid, run_id = start(search, store)
    store.add_directive(cid, "pause", sticky=True)
    store.add_directive(cid, "choose", {"index": 1})
    nodes = search.run(cid, run_id)["nodes"]

    assert nodes[0]["system"] == "Fe-Ni"
    assert nodes[0]["move"]["selected_by"] == "researcher"
    kinds = [e["kind"] for e in store.events(cid)]
    assert kinds.index("paused") < kinds.index("resumed") < kinds.index("selected")
    # The pause is still held for the next step; the choice is spent.
    assert [d["kind"] for d in store.directives(cid)] == ["pause"]


def test_pause_times_out_back_to_the_harness():
    store = Store(":memory:")
    search, _ = make_search(
        store, [[move(system="Co-Fe")]], max_steps=1, pause_timeout_seconds=0
    )
    cid, run_id = start(search, store)
    store.add_directive(cid, "pause", sticky=True)
    nodes = search.run(cid, run_id)["nodes"]
    assert nodes[0]["move"]["selected_by"] == "temperature"


def test_stop_and_grant():
    store = Store(":memory:")
    search, _ = make_search(
        store, [[move(system="Co-Fe")]], max_steps=3, max_expensive_evals=1
    )
    cid, run_id = start(search, store)
    store.add_directive(cid, "grant", {"expensive": 2})
    store.add_directive(cid, "stop")
    result = search.run(cid, run_id)

    assert result["nodes"] == []
    events = store.events(cid)
    finished = next(e for e in events if e["kind"] == "run_finished")
    assert finished["data"]["stopped_by_researcher"] is True
    grant = next(
        e for e in events
        if e["kind"] == "directive_applied" and e["data"]["kind"] == "grant"
    )
    assert grant["data"]["extra"] == 2
    assert store.directives(cid) == []


def test_retracted_learning_leaves_the_record():
    store = Store(":memory:")
    search, llm = make_search(
        store, [[move(system="Co-Fe")], [move(system="Fe-Ni")]], max_steps=1
    )
    cid, run_id = start(search, store)
    search.run(cid, run_id)
    learning = store.learnings(cid)[0]
    store.retract_learning(learning["id"], "wrong")

    assert store.learnings(cid) == []
    assert store.learnings(cid, include_retracted=True)[0]["retracted"] == 1
    search.run(cid, store.start_run(cid))
    assert "LEARNED-SOMETHING" not in prompts(llm)[1].split("## Search state")[0]
