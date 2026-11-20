#!/usr/bin/env python3
"""Enter a measured candidate on a quest without running its measurements
twice.

    quest.py plan <file-id> [--quest ID]
    quest.py fill <file-id> --campaign <slug> [--quest ID] [--steps a,b]
    quest.py submit <file-id> --note "<one paragraph>" [--quest ID]

A quest's evaluator runs routes as steps. Before it starts a step, Ouro looks
for a run of yours of the same route, made with the same value for every
setting the step fixes (a setting you didn't send counts at the route's
default), either on the same input files or, for a step marked
`idempotent` (a relax), the run that made its input. If it finds one, the
step takes it over and nothing runs. Settings the step leaves out, such as
`initial_magmoms`, are yours to choose.

On the GHOST magnets quest that means: submit the file the campaign's DFT
relax made (DZP, kspacing 0.2), and the quest's DFT relax is that run; its
moments, anisotropy and Curie steps then read that same file.

So submit the file the campaign measured, by its id, never a re-upload: a
copy is a new asset and matches nothing.

`plan` reads the quest's evaluator and says, step by step, which of your
earlier runs each step would take over and which would run. A step that
reads another step's output is followed through the run it would reuse; one
whose input only exists once the evaluation runs is "made during the
evaluation". Gates are not judged here.

`fill` runs the steps that read the submitted file directly and would run,
with the step's own settings and the campaign's starting moments (from the
run that made the file, when it sent them), and waits for them. Then the
evaluation takes them over. Use it when the evaluator's default moments
might land in another magnetic state. `--steps` limits it to named steps.

`submit` makes the entry: the file, under the quest's submission name, and
the note.

The default quest is the GHOST "Rare-earth-free magnets" leaderboard.

Run with the materials pyenv: ~/.pyenv/versions/materials/bin/python.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor

import labtools

GHOST_MAGNETS = "01a1f1d0-2341-7e57-890f-eb24976a2825"
SUBMITTED = "inputs"


def dump(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def evaluator(ouro, quest_id: str) -> tuple[str, dict]:
    """(item id, evaluator definition) of the quest's one scored item."""
    body = ouro.client.get(f"/quests/{quest_id}/items").json()
    items = body.get("data") or []
    for item in items if isinstance(items, list) else []:
        definition = item.get("evaluator") or {}
        definition = definition.get("definition", definition)
        if isinstance(definition, dict) and definition.get("steps"):
            return item["id"], definition
    sys.exit(f"Quest {quest_id} has no item with an evaluator.")


def submission_key(definition: dict) -> str:
    for step in definition["steps"].values():
        for ref in (step.get("inputs") or {}).values():
            if isinstance(ref, str) and ref.startswith(SUBMITTED + "."):
                return ref.split(".", 1)[1]
    sys.exit("The evaluator reads no submitted asset.")


def defaults(ouro, route_id: str, cache: dict) -> dict:
    """{setting: default} from a route's request body schema."""
    if route_id not in cache:
        data = ouro.client.get(f"/routes/{route_id}").json().get("data") or {}
        body = (data.get("route") or data).get("request_body") or {}
        schema = (body.get("content") or {}).get("application/json", {}).get("schema", body)
        cache[route_id] = {
            key: prop.get("default")
            for key, prop in (schema.get("properties") or {}).items()
            if isinstance(prop, dict)
        }
    return cache[route_id]


def same_settings(fixed: dict, sent: dict, route_defaults: dict) -> bool:
    for key, value in (fixed or {}).items():
        used = sent[key] if key in (sent or {}) else route_defaults.get(key)
        if json.dumps(value, sort_keys=True) != json.dumps(used, sort_keys=True):
            return False
    return True


def read_path(response, path: list[str]):
    """The asset id at a step output path, e.g. ["cif"] -> response.cif.id."""
    for root in (response, (response or {}).get("data") if isinstance(response, dict) else None):
        value = root
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(value, dict) and value.get("id"):
            return value["id"]
        if isinstance(value, str):
            return value
    return None


def runs_on(ouro, asset_id: str, cache: dict) -> tuple[list[dict], dict | None]:
    """(successful runs that read the asset, the run that made it)."""
    if asset_id not in cache:
        found = dump(ouro.assets.actions(asset_id, role="both", include_response=True))
        rows = [row for row in found.get("as_input") or [] if row.get("status") == "success"]
        cache[asset_id] = (rows, found.get("created_by"))
    return cache[asset_id]


def plan(ouro, file_id: str, definition: dict, me: str) -> dict:
    """{step: {"inputs", "reuse" (action id or None), "made_later", "body"}}."""
    steps = definition["steps"]
    route_cache, asset_cache, out = {}, {}, {}

    def resolve(name: str, trail=()):
        if name in out:
            return out[name]
        step = steps[name]
        inputs, made_later = {}, False
        for input_name, ref in (step.get("inputs") or {}).items():
            if isinstance(ref, dict):
                inputs[input_name] = ref["asset_id"]
                continue
            root, *path = ref.split(".")
            if root == SUBMITTED:
                inputs[input_name] = file_id
                continue
            if root in trail:
                made_later = True
                continue
            upstream = resolve(root, trail + (name,))
            asset = read_path(upstream.get("response"), path) if upstream["reuse"] else None
            if asset:
                inputs[input_name] = asset
            else:
                made_later = True
        reuse, response = None, None
        route_defaults = defaults(ouro, step["route_id"], route_cache) if inputs else {}

        def own(row) -> bool:
            sent = (row.get("metadata") or {}).get("body") or {}
            return (row.get("status") == "success" and row.get("route_id") == step["route_id"]
                    and row.get("user_id") == me
                    and same_settings(step.get("body") or {}, sent, route_defaults))

        if not made_later and step.get("idempotent") and len(inputs) == 1:
            _, made_by = runs_on(ouro, next(iter(inputs.values())), asset_cache)
            if made_by and own(made_by):
                reuse, response = made_by["id"], made_by.get("response")
        if not reuse and not made_later and inputs:
            rows, _ = runs_on(ouro, next(iter(inputs.values())), asset_cache)
            for row in sorted(rows, key=lambda r: r.get("created_at") or "", reverse=True):
                if not own(row):
                    continue
                read = {i.get("name"): (i.get("asset") or {}).get("id") for i in row.get("input_assets") or []}
                if read == inputs:
                    reuse, response = row["id"], row.get("response")
                    break
        out[name] = {"inputs": inputs, "reuse": reuse, "response": response,
                     "made_later": made_later, "body": step.get("body") or {},
                     "route_id": step["route_id"]}
        return out[name]

    for name in steps:
        resolve(name)
    return out


def whoami(ouro) -> str:
    user_id = getattr(getattr(ouro, "user", None), "id", None)
    if not user_id:
        sys.exit("Could not tell which user this key belongs to.")
    return str(user_id)


def show(steps: dict) -> None:
    for name, step in steps.items():
        if step["reuse"]:
            state = f"takes over {step['reuse']}"
        elif step["made_later"]:
            state = "runs (its input is made during the evaluation)"
        else:
            state = "runs"
        print(f"  {name:<12} {state}")
    ran = [name for name, step in steps.items() if not step["reuse"]]
    print(f"\n{len(steps) - len(ran)} of {len(steps)} steps taken over; would run: {', '.join(ran) or 'nothing'}")


def cmd_plan(args) -> None:
    ouro = labtools.client()
    _, definition = evaluator(ouro, args.quest)
    show(plan(ouro, args.file, definition, whoami(ouro)))


def cmd_fill(args) -> None:
    ouro = labtools.client()
    org, team = labtools.where(args.campaign)
    _, definition = evaluator(ouro, args.quest)
    steps = plan(ouro, args.file, definition, whoami(ouro))
    wanted = set(args.steps.split(",")) if args.steps else None
    todo = {
        name: step for name, step in steps.items()
        if not step["reuse"] and not step["made_later"]
        and all(value == args.file for value in step["inputs"].values())
        and (wanted is None or name in wanted)
    }
    if not todo:
        print("Nothing to run: every step that reads the file is taken over, or made during the evaluation.")
        return
    _, made_by = runs_on(ouro, args.file, {})
    seeds = ((made_by or {}).get("metadata") or {}).get("body", {}).get("initial_magmoms")
    started, route_cache = {}, {}
    for name, step in todo.items():
        body = dict(step["body"])
        takes_seeds = "initial_magmoms" in defaults(ouro, step["route_id"], route_cache)
        if seeds and takes_seeds and "initial_magmoms" not in body:
            body["initial_magmoms"] = seeds
        action = ouro.routes.execute(
            step["route_id"], body=body, input_assets=dict(step["inputs"]),
            wait=False, notify="failure", org_id=org, team_id=team,
        )
        started[name] = action.id
        print(f"  {name:<12} started {action.id}" + ("  (campaign moments)" if "initial_magmoms" in body else ""))
    with ThreadPoolExecutor(len(started)) as pool:
        done = dict(zip(started, pool.map(lambda aid: labtools.wait(ouro, aid, timeout=7200), started.values())))
    for name, action in done.items():
        status = action.status if action else "still running (look it up later)"
        print(f"  {name:<12} {status}")
    print("\nThen: quest.py plan, and submit once the steps you need are taken over.")


def cmd_submit(args) -> None:
    ouro = labtools.client()
    item_id, definition = evaluator(ouro, args.quest)
    entry = ouro.quests.create_entry(
        args.quest, item_id=item_id,
        assets={submission_key(definition): args.file}, description=args.note,
    )
    entry = dump(entry)
    print(f"entry {entry.get('id')}: {entry.get('status')}, evaluation {entry.get('eval_status')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "fill", "submit"):
        p = sub.add_parser(name)
        p.add_argument("file", help="the measured file's asset id")
        p.add_argument("--quest", default=GHOST_MAGNETS)
        if name == "fill":
            p.add_argument("--campaign", required=True)
            p.add_argument("--steps", help="comma-separated step names (default: every one that would run)")
        if name == "submit":
            p.add_argument("--note", required=True)
    args = parser.parse_args()
    {"plan": cmd_plan, "fill": cmd_fill, "submit": cmd_submit}[args.cmd](args)


if __name__ == "__main__":
    main()
