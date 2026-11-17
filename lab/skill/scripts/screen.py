#!/usr/bin/env python3
"""Screen a batch of CIFs on the hull route: relax with Orb v3, place on the
hull, keep the relaxed cell. Free, and nothing becomes an Ouro asset: inputs
go up as scratch uploads (learnings L201, L203).

    screen.py run <cifs-dir | queue.json> --campaign <slug> --out work/e040 [-n 16]
    screen.py pbe --campaign <slug> --out work/e040 [--cut 25]
    screen.py tally --out work/e040

`run` writes <out>/state.json (one entry per cell: the action id beside its
result) and <out>/relaxed/<name>.cif. It resumes: rerun the same command
after a crash and only unfinished cells are sent. A failed cell is unknown,
not unstable; it is retried on the next run.

`pbe` puts the relaxed cells within --cut meV/atom on the published-PBE
surface (the cell is still scored by Orb, the references are published PBE).
It ranked candidates better than the Orb surface did in sun-tetragonal, and
it names the PBE competitors (learnings L199, L201).

Then: crystal.py classify <out>/relaxed --screen <out>/state.json --cut 25

A queue.json is a list of {"name", "cif", "batch"}; a folder is every *.cif
in it. Order matters when cells compete: each submitted cell joins the
references, so send parents and empty variants before their decorations.

Run with the materials pyenv: ~/.pyenv/versions/materials/bin/python.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import labtools

HULL_ROUTE = "75fe7f4b-22f5-4883-aa1d-378fd42da47c"
KEEP = (
    "formula", "e_above_hull", "decomposition", "formation_energy_per_atom", "stored",
    "input_is_lowest_energy", "same_composition_entries", "lowest_energy_at_composition",
    "space_group", "space_group_number", "references_by_source",
)


class State:
    """state.json, safe to update from many threads."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict = json.loads(path.read_text()) if path.exists() else {}
        self.lock = threading.Lock()
        self.saved = 0.0

    def set(self, name: str, **fields) -> None:
        with self.lock:
            self.data.setdefault(name, {}).update(fields)
            if time.time() - self.saved > 2:
                self._write()

    def _write(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data))
        os.replace(tmp, self.path)
        self.saved = time.time()

    def save(self) -> None:
        with self.lock:
            self._write()


def read_queue(source: str) -> list[dict]:
    path = Path(source)
    if path.is_dir():
        return [dict(name=p.stem, cif=str(p), batch=path.name) for p in sorted(path.glob("*.cif"))]
    return json.loads(path.read_text())


def run_batch(queue: list[dict], state: State, out: Path, body: dict, org: str, team: str, workers: int) -> None:
    ouro = labtools.client()
    relaxed = out / "relaxed"
    relaxed.mkdir(parents=True, exist_ok=True)
    todo = [c for c in queue if state.data.get(c["name"], {}).get("status") != "success"]
    print(f"{len(queue)} cells, {len(todo)} to run, {workers} at a time", flush=True)
    done = collections.Counter()

    def one(cell: dict) -> None:
        name = cell["name"]
        previous = state.data.get(name, {})
        action_id = previous.get("action_id") if previous.get("status") == "running" else None
        started = time.time()
        if action_id is None:
            error = None
            for attempt in range(3):
                try:
                    action = ouro.routes.execute(
                        HULL_ROUTE, body=body, input_assets={"structure": Path(cell["cif"])},
                        wait=False, notify="none", org_id=org, team_id=team,
                    )
                    action_id = str(action.id)
                    break
                except Exception as e:
                    error = repr(e)[:300]
                    time.sleep(5 * (attempt + 1))
            if action_id is None:
                state.set(name, batch=cell.get("batch"), status="submit_error", error=error)
                done["submit_error"] += 1
                return
            state.set(name, batch=cell.get("batch"), action_id=action_id, status="running")
        action = labtools.wait(ouro, action_id)
        if action is None:
            done["still running"] += 1  # left as running; the next run picks it up by action id
            return
        data = labtools.payload(action)
        result = {key: data.get(key) for key in KEEP}
        if action.status != "success":
            result["error"] = json.dumps(data.get("error") or data)[:400]
        if data.get("relaxed_cif"):
            (relaxed / f"{name}.cif").write_text(data["relaxed_cif"])
        state.set(name, status=action.status, result=result, seconds=round(time.time() - started))
        done[action.status] += 1
        if sum(done.values()) % 50 == 0:
            print(time.strftime("%H:%M"), dict(done), flush=True)

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, todo))
    state.save()
    print(dict(done) or "nothing to do")


def tally(state: State) -> None:
    by_batch = collections.defaultdict(list)
    for entry in state.data.values():
        by_batch[entry.get("batch")].append(entry)
    for batch, cells in sorted(by_batch.items(), key=lambda kv: str(kv[0])):
        ok = [c["result"]["e_above_hull"] for c in cells if c.get("status") == "success"]
        statuses = dict(collections.Counter(c.get("status") for c in cells))
        print(
            f"{batch}: {len(cells)} cells {statuses}; on hull {sum(e <= 1e-4 for e in ok)}, "
            f"within 25 meV {sum(e <= 0.025 for e in ok)}, within 50 meV {sum(e <= 0.05 for e in ok)}"
        )
    failed = sum(1 for c in state.data.values() if c.get("status") != "success")
    if failed:
        print(f"{failed} cells have no result: unknown, not unstable. Rerun to retry them.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("queue", help="a folder of CIFs or a queue.json")
    p.add_argument("-n", "--workers", type=int, default=16)
    q = sub.add_parser("pbe")
    q.add_argument("--cut", type=float, default=25.0, help="meV/atom on the Orb surface")
    q.add_argument("-n", "--workers", type=int, default=8)
    t = sub.add_parser("tally")
    for command in (p, q, t):
        command.add_argument("--out", required=True, help="the experiment's work folder")
    for command in (p, q):
        command.add_argument("--campaign", required=True, help="where runs go is read from its campaign.md")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    state = State(out / "state.json")

    if args.cmd == "tally":
        tally(state)
        return
    org, team = labtools.where(args.campaign)
    if args.cmd == "run":
        run_batch(read_queue(args.queue), state, out, {"allow_unrelaxed": True}, org, team, args.workers)
        tally(state)
    else:
        near = [
            dict(name=name, cif=str(out / "relaxed" / f"{name}.cif"), batch="pbe-surface")
            for name, entry in state.data.items()
            if entry.get("status") == "success" and entry["result"]["e_above_hull"] * 1000 <= args.cut
            and (out / "relaxed" / f"{name}.cif").exists()
        ]
        pbe = State(out / "state_pbe.json")
        # allow_unrelaxed: the published geometry check is against Orb forces, and these cells are Orb-relaxed.
        run_batch(near, pbe, out / "pbe", {"energy_surface": "pbe", "allow_unrelaxed": True}, org, team, args.workers)
        rows = sorted(
            (state.data[n]["result"]["e_above_hull"] * 1000, e["result"]["e_above_hull"] * 1000, n, e["result"]["decomposition"])
            for n, e in pbe.data.items() if e.get("status") == "success"
        )
        for orb, published, name, decomposition in rows:
            print(f"{orb:6.1f} orb  {published:6.1f} published-PBE  {name}  -> {', '.join(decomposition or {})}")


if __name__ == "__main__":
    main()
