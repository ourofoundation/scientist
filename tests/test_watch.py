"""The watch server: state for the page, and directives from it."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from scientist.core.store import Store
from scientist.watch import Watcher, make_handler
from http.server import ThreadingHTTPServer

from test_search import make_search, move, start


@pytest.fixture
def served():
    store = Store(":memory:")
    search, _ = make_search(store, [[move(system="Co-Fe")]], max_steps=1)
    cid, run_id = start(search, store)
    search.run(cid, run_id)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Watcher(store)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield store, cid, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def call(url, body=None, content_type="application/json"):
    request = urllib.request.Request(url)
    if body is not None:
        request.data = json.dumps(body).encode()
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw.decode()


def test_page_and_state(served):
    store, cid, base = served
    status, page = call(base + "/")
    assert status == 200 and "<title>Scientist</title>" in page

    status, campaigns = call(base + "/api/campaigns")
    assert campaigns[0]["id"] == cid and campaigns[0]["steps_done"] == 1

    status, state = call(f"{base}/api/state?campaign={cid}")
    assert status == 200
    assert [n["system"] for n in state["nodes"]] == ["Co-Fe"]
    assert state["objective"]["criteria"][0]["unit"] == "eV/atom"
    candidate = state["candidates"][0]
    assert "cif" not in candidate and "ceiling" in candidate and "missing" in candidate
    assert state["last_event_id"] > 0

    status, events = call(f"{base}/api/events?campaign={cid}")
    assert events[0]["kind"] == "run_started" and events[-1]["kind"] == "run_finished"
    status, none = call(f"{base}/api/events?campaign={cid}&after={events[-1]['id']}")
    assert none == []
    assert call(f"{base}/api/state?campaign=999")[0] == 404


def test_directives_round_trip(served):
    store, cid, base = served
    post = lambda path, body, **kw: call(base + path, body, **kw)

    status, made = post("/api/directive", {"campaign_id": cid, "kind": "steer",
                                           "payload": {"text": "go Fe-rich"}, "sticky": True})
    assert status == 200
    directive = store.directives(cid)[0]
    assert directive["payload"]["text"] == "go Fe-rich" and directive["sticky"] == 1

    # Pausing twice holds one pause; pause is always sticky.
    post("/api/directive", {"campaign_id": cid, "kind": "pause"})
    post("/api/directive", {"campaign_id": cid, "kind": "pause"})
    pauses = [d for d in store.directives(cid) if d["kind"] == "pause"]
    assert len(pauses) == 1 and pauses[0]["sticky"] == 1

    assert post("/api/directive/cancel", {"id": made["id"]})[0] == 200
    assert post("/api/directive/cancel", {"id": made["id"]})[0] == 404
    assert [d["kind"] for d in store.directives(cid)] == ["pause"]

    assert post("/api/directive", {"campaign_id": cid, "kind": "steer", "payload": {}})[0] == 400
    assert post("/api/directive", {"campaign_id": cid, "kind": "delete-everything"})[0] == 400
    assert post("/api/directive", {"campaign_id": 999, "kind": "stop"})[0] == 404
    # Only JSON posts are accepted, which a cross-site form cannot send.
    assert post("/api/directive", {"campaign_id": cid, "kind": "stop"},
                content_type="text/plain")[0] == 415

    learning = store.learnings(cid)[0]
    assert post("/api/learning/retract", {"id": learning["id"], "reason": "wrong"})[0] == 200
    assert store.learnings(cid) == []
    kinds = [e["kind"] for e in store.events(cid)]
    assert "directive_added" in kinds and "learning_retracted" in kinds


def test_goal_creation(served, monkeypatch):
    from scientist import watch
    from scientist.core.config import ScientistConfig
    from test_search import OBJECTIVE

    store, cid, base = served
    watcher = watch.Watcher(store, ScientistConfig(llm_api_key="k", db_path=":memory:"))
    watcher.config.use_sim(3)
    spawned = []
    monkeypatch.setattr(
        watcher, "_spawn", lambda c, steps, exp: spawned.append((c["key"], steps, exp)) or "log"
    )
    monkeypatch.setattr(watch, "compile_objective", lambda llm, goal: OBJECTIVE.model_copy(deep=True))
    monkeypatch.setattr("scientist.core.llm.LLM", lambda config: object())

    info = watcher.info()
    assert info["sim"] and info["can_run"] and info["catalog"][0]["name"] == "e_hull"

    assert watcher.compile({"goal": "short"})[0] == 400
    status, drafted = watcher.compile({"goal": "find a brand new magnet please"})
    assert status == 200 and drafted["existing_campaign"] is None
    objective = drafted["objective"]

    bad = {**objective, "criteria": [{**objective["criteria"][1], "target": 0}]}
    assert watcher.create_campaign({"goal": "find a brand new magnet please", "objective": bad})[0] == 400
    none = {**objective, "criteria": []}
    assert watcher.create_campaign({"goal": "find a brand new magnet please", "objective": none})[0] == 400

    objective["criteria"][1]["target"] = 750
    body = {"goal": "find a brand new magnet please", "objective": objective, "steps": 4, "expensive": 0}
    status, made = watcher.create_campaign(body)
    assert status == 200
    campaign = store.campaign(made["campaign_id"])
    assert campaign["key"].startswith("sim-")
    assert campaign["objective"]["criteria"][1]["target"] == 750
    assert spawned == [(campaign["key"], 4, 0)]
    assert watcher.create_campaign(body)[0] == 409

    assert watcher.start_run({"campaign_id": campaign["id"], "steps": 999})[0] == 400
    assert watcher.start_run({"campaign_id": 12345})[0] == 404
    assert watcher.start_run({"campaign_id": campaign["id"], "steps": 2})[0] == 200
    assert spawned[-1] == (campaign["key"], 2, watcher.config.max_expensive_evals)

    command = watch.run_command(watcher.config, "a goal", "sim-abc", 4, 0)
    assert command[1:4] == ["-m", "scientist.main", "a goal"]
    assert command[-3:] == ["--sim", "--seed", "3"] and "--expensive" in command


def test_runner_reports_a_failed_start(served, tmp_path):
    import subprocess
    import sys
    from scientist import watch

    store, cid, base = served
    watcher = watch.Watcher(store)
    log = tmp_path / "run.log"
    log.write_text("boom: missing key\n")
    process = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
    process.wait()
    watcher.children[cid] = (process, str(log))
    runner = watcher.runner(cid)
    assert runner == {"running": False, "started_here": True, "exit_code": 3,
                      "log": str(log), "log_tail": "boom: missing key\n"}
    assert watcher.runner(999) == {"running": False, "started_here": False}
