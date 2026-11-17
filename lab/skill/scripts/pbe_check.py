#!/usr/bin/env python3
"""A PBE stability check that means something (learnings L198, L199, L202).

The Orb hull can be 30 meV/atom off on a new cell in either direction, so an
ML-only candidate is a candidate for this check and nothing more. The check:
single points (or relaxations) on the target and on everything it could
decompose into, then the lowest-energy combination of the other phases.

    pbe_check.py plan target.cif [more.cif] --out work/e041 [--add label=cell.cif] [--basis TZDP]
    pbe_check.py submit --out work/e041 --campaign <slug> --yes [--relax]
    pbe_check.py collect --out work/e041
    pbe_check.py balance --out work/e041

`plan` costs nothing. It takes the competitors from BOTH the Orb and the
published-PBE hull in crystal-db (they name different phases), fetches their
cells, reduces every cell to its primitive, writes the sites in the order the
DFT routes read a CIF (elements by electronegativity, pymatgen's sorted order:
Ti, Mn, Al, not Al, Mn, Ti), and prints a cost range. Each entry of plan.json
lists that order as `site_elements`. Read the plan before submitting:
  - add any competitor no database lists (a carbon-filled or emptied variant
    of a neighbour): that is where sun-tetragonal's best compound came from;
  - a weakly magnetic or mixed-sign phase may need `"body": {"mixing_beta": 0.1,
    "initial_magmoms": [...]}` on its entry in plan.json, and a phase known
    to be non-magnetic `"body": {"nspin": "non_spin"}` (L213). The route
    applies `initial_magmoms` one per site in its read order, which is the
    entry's `site_elements` (L245): a seed list in any other order puts the
    moments on the wrong atoms and the run still succeeds;
  - a parent with a known antiferromagnetic order needs that state on both
    sides of the balance.

`submit` spends money (DFT) and makes the inputs private file assets, which
the DFT routes require. It needs --yes, and the campaign's budget rules still
apply. A run that hits the 3600 s cap stops uncharged: submit again with the
same body and it resumes from the saved density.

`balance` reports each target against the lowest combination of the other
computed phases, compound-only first. A balance with an elemental reference
in it is flagged: this LCAO setup is not calibrated for elements (Al is off
by about 190 meV per atom). Compare phases only between runs that ended in
the same magnetic state; `collect` prints the total moment and, for every
seeded run, the seeds as the route applied them and the moments reached, per
element (`collect --refresh` reads finished runs again). Check that line
before using a seeded run: a seed that sits on Al, or that ended at zero, is
there to be seen.

Run with the materials pyenv: ~/.pyenv/versions/materials/bin/python.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
from pymatgen.core import Composition, Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

import labtools

SCF_ROUTE = "e4ed9888-0c34-4a9d-a227-02fa9d7521e0"  # ground-state SCF, $0.0003/s
RELAX_ROUTE = "d180f520-fc88-48b7-9eca-3d123f5cf680"  # DFT relaxation, $0.0006/s
RATE = {"scf": 0.0003, "relax": 0.0006}
# DZP is the lab default (Matt, 2026-11-13). Every phase of one balance must share a basis:
# energies from earlier TZDP checks (sun-tetragonal E026, E029, E044) cannot be mixed in.
SETTINGS = {"basis_size": "DZP", "kspacing": 0.2}
CAP_SECONDS = 3600


def prepared(structure: Structure) -> Structure:
    """The primitive cell, its sites in the order the DFT routes read a CIF.

    The routes read a CIF with pymatgen, which groups the sites by element
    and sorts the elements by electronegativity, and they apply
    `initial_magmoms` one per site in that order. Writing the file in the
    same order makes the file, plan.json's `site_elements` and a hand-written
    seed list agree (learnings L245; the alphabetical order this used to
    write did not)."""
    primitive = SpacegroupAnalyzer(structure, symprec=0.01).find_primitive() or structure
    primitive = primitive.copy()
    primitive.remove_oxidation_states()
    sites = sorted(primitive, key=lambda s: (s.specie.X, s.specie.symbol, *np.round(s.frac_coords % 1, 4)))
    return Structure.from_sites(sites)


def read_order(path: str | Path) -> list[str]:
    """The element of each site in the order the DFT routes read this CIF."""
    return [site.specie.symbol for site in Structure.from_file(str(path))]


def seconds_range(natoms: int) -> tuple[float, float]:
    """Single-point wall time seen so far at TZDP, kspacing 0.2: 14-75 s per
    atom up to 25 atoms, 36-180 above (sun-tetragonal E012-E029). Symmetry
    and magnetism move a cell across that range, so it is a range. DZP, the
    default now, runs faster, so read this as an upper range until DZP
    timings replace it."""
    low, high = (14, 75) if natoms <= 25 else (36, 180)
    return natoms * low, natoms * high


def plan(args) -> None:
    import crystal

    out = Path(args.out)
    cells_dir = out / "cells"
    cells_dir.mkdir(parents=True, exist_ok=True)
    cells: dict[str, dict] = {}

    def add(label: str, structure: Structure, role: str, source: str) -> None:
        cell = prepared(structure)
        path = cells_dir / f"{label}.cif"
        cell.to(filename=str(path))
        back = Structure.from_file(str(path))
        elements = [site.specie.symbol for site in back]
        shift = back.frac_coords - cell.frac_coords
        if elements != [site.specie.symbol for site in cell] or np.abs(shift - np.round(shift)).max() > 1e-5:
            sys.exit(f"{path}: the file reads back in a different site order than it was written in; seeds would be misplaced.")
        low, high = seconds_range(len(cell))
        cells[label] = dict(
            label=label, role=role, formula=cell.composition.reduced_formula, natoms=len(cell),
            sg=SpacegroupAnalyzer(cell, symprec=0.1).get_space_group_symbol(), source=source,
            cif=str(path), seconds=[low, high], site_elements=elements, body={},
        )

    targets = []
    for path in args.targets:
        structure = Structure.from_file(path)
        label = structure.composition.reduced_formula.replace("(", "").replace(")", "")
        add(label, structure, "target", path)
        targets.append((label, path))
    for label, path in targets:
        found = crystal.competitors(path, published=args.published, refresh=True)
        system = crystal.chemsys(path)
        for surface, rows in found.items():
            db = crystal.database(system, "orb")
            for row in rows:
                key = row["formula"].replace("(", "").replace(")", "")
                if any(c["formula"] == row["formula"] and c["sg"] == row["space_group"] for c in cells.values()):
                    continue
                cif = crystal.cif_of(db, row["structure_id"])
                if cif is None:
                    print(f"  no geometry stored for {row['formula']} ({row['source_id']}): add it by hand")
                    continue
                if key in cells:
                    key = f"{key}_{row['space_group'].replace('/', '')}"
                role = "element" if len(Composition(row["formula"]).elements) == 1 else "competitor"
                add(key, Structure.from_str(cif, fmt="cif"), role, f"{row['source']}:{row['source_id']} ({surface} hull of {label})")
    for item in args.add or []:
        label, path = item.split("=", 1)
        add(label, Structure.from_file(path), "competitor", f"added by hand: {path}")

    settings = {**SETTINGS, "basis_size": args.basis}
    (out / "plan.json").write_text(json.dumps(dict(settings=settings, cells=list(cells.values())), indent=1))
    if args.basis != SETTINGS["basis_size"]:
        print(f"Basis {args.basis} for every phase of this plan. The default is {SETTINGS['basis_size']}: this needs the user's word.")
    low = sum(c["seconds"][0] for c in cells.values())
    high = sum(c["seconds"][1] for c in cells.values())
    for c in cells.values():
        flag = "  over the 3600 s cap at the slow end: expect a resume" if c["seconds"][1] > CAP_SECONDS else ""
        print(f"{c['role']:10s} {c['label']:22s} {c['natoms']:3d} atoms  {c['sg']:10s} {c['source']}{flag}")
    print(
        f"\n{len(cells)} single points, about ${low * RATE['scf']:.2f}-{high * RATE['scf']:.2f} "
        f"(relaxations: several times that; they moved margins by under 2 meV/atom so far)."
    )
    if any(c["role"] == "element" for c in cells.values()):
        print("An elemental reference is in the set: its balance is uncalibrated here (L199).")
    print(f"Plan: {out / 'plan.json'}. Add unlisted competitors with --add before submitting.")


def load(out: Path) -> tuple[dict, dict, Path]:
    plan_path = out / "plan.json"
    if not plan_path.exists():
        sys.exit(f"No plan at {plan_path}: run `pbe_check.py plan` first.")
    state_path = out / "pbe_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    return json.loads(plan_path.read_text()), state, state_path


def submit(args) -> None:
    out = Path(args.out)
    planned, state, state_path = load(out)
    kind = "relax" if args.relax else "scf"
    todo = [c for c in planned["cells"] if state.get(c["label"], {}).get(kind, {}).get("status") != "success"]
    if not args.yes:
        sys.exit(f"{len(todo)} DFT runs would start. This spends money: pass --yes once the budget allows it.")
    ouro = labtools.client()
    org, team = labtools.where(args.campaign)
    for cell in todo:
        entry = state.setdefault(cell["label"], {})
        if "file" not in entry:
            created = ouro.files.create(
                name=f"{cell['formula']}: PBE check input ({args.campaign} {out.name})",
                description=f"{cell['role']} in a PBE stability check; {cell['source']}. Primitive cell, sites in the route's read order.",
                visibility="private", file_path=cell["cif"], org_id=org, team_id=team,
            )
            entry["file"] = str(created.id)
        body = {**planned["settings"], **cell.get("body", {})}
        seeds = body.get("initial_magmoms")
        if seeds is not None:
            elements = read_order(cell["cif"])
            if len(seeds) != len(elements):
                sys.exit(f"{cell['label']}: {len(seeds)} seeds for {len(elements)} sites.")
            print(f"{cell['label']}: seeding {per_element(elements, seeds)}")
        action = ouro.routes.execute(
            RELAX_ROUTE if args.relax else SCF_ROUTE, body=body, input_assets={"file": entry["file"]},
            wait=False, notify="failure", org_id=org, team_id=team,
        )
        entry[kind] = dict(action_id=str(action.id), status="running", body=body)
        state_path.write_text(json.dumps(state, indent=1))
        print(f"{cell['label']}: file {entry['file']}, action {action.id}")
    print("Record the action ids in experiments.jsonl now, then `pbe_check.py collect` when they finish.")


def per_element(elements: list[str], values: list[float]) -> str:
    """'Mn [3.0, -3.0]  Al [~0 x5]': values grouped by element; a group with
    nothing above 0.1 is folded."""
    groups: dict[str, list[float]] = {}
    for element, value in zip(elements, values):
        groups.setdefault(element, []).append(round(float(value), 2))
    return "  ".join(
        f"{element} [~0 x{len(v)}]" if max(abs(x) for x in v) < 0.1 else f"{element} {[x + 0 for x in v]}"
        for element, v in groups.items()
    )


def seed_report(run: dict) -> str | None:
    """For a seeded run: the seeds as the route applied them and the moments
    it reached, per element, with a warning when a seeded element ended at
    zero (a collapsed state, or seeds on the wrong atoms)."""
    applied = run.get("magnetic_sublattice") or {}
    if run.get("body", {}).get("initial_magmoms") is None or not applied.get("initial_magmoms_elements"):
        return None
    elements, seeds = applied["initial_magmoms_elements"], applied["initial_magmoms_uB"]
    line = f"seeded {per_element(elements, seeds)}"
    moments = run.get("site_moments") or []
    if moments:
        line += f"  ->  {per_element([m['element'] for m in moments], [m['moment_uB'] for m in moments])}"
        reached: dict[str, float] = {}
        for m in moments:
            reached[m["element"]] = max(reached.get(m["element"], 0.0), abs(m["moment_uB"]))
        lost = sorted({e for e, seed in zip(elements, seeds) if abs(seed) > 0.5 and reached.get(e, 0.0) < 0.05})
        if lost:
            line += f"  CHECK: the seed on {', '.join(lost)} ended at zero (collapsed, or seeds on the wrong atoms: L245)"
    return line


def collect(args) -> None:
    out = Path(args.out)
    _, state, state_path = load(out)
    ouro = labtools.client()
    for label, entry in state.items():
        for kind in ("scf", "relax"):
            run = entry.get(kind)
            if not run:
                continue
            if args.refresh or run["status"] not in labtools.TERMINAL:
                action = ouro.routes.retrieve_action(run["action_id"])
                run["status"] = action.status
                if action.status in labtools.TERMINAL:
                    data = labtools.payload(action)
                    run["result"] = {k: v for k, v in data.items() if not isinstance(v, (list, dict))}
                    run["site_moments"] = data.get("site_moments")
                    run["magnetic_sublattice"] = data.get("magnetic_sublattice")
                    started, finished = getattr(action, "started_at", None), getattr(action, "finished_at", None)
                    if started and finished:
                        run["seconds"] = (finished - started).total_seconds()
                        run["cost_usd_estimated"] = round(run["seconds"] * RATE[kind], 3)
            result = run.get("result", {})
            energy = result.get("final_energy_ev", result.get("total_energy_ev"))
            moment = result.get("final_total_mag_uB", result.get("total_magnetic_moment_uB"))
            print(
                f"{label:22s} {kind:5s} {run['status']:10s} "
                + (f"E {energy:.4f} eV  M {moment} uB  fmax {result.get('max_force_ev_a')}  ${run.get('cost_usd_estimated')}" if energy is not None else "")
                + ("  not charged; resubmit with the same body to resume" if run["status"] == "timed-out" else "")
            )
            report = seed_report(run)
            if report:
                print(f"{'':22s} {report}")
    state_path.write_text(json.dumps(state, indent=1))
    spent = sum(r.get("cost_usd_estimated") or 0 for e in state.values() for r in (e.get("scf"), e.get("relax")) if r)
    print(f"\nEstimated spend so far: ${spent:.2f} (seconds x rate; record it with cost_estimated).")


def lowest_combination(energies: dict[str, tuple[Composition, float]], target: str, pool: list[str]):
    """Energy above (+) or below (-) the lowest mix of `pool` at the target's
    composition, in meV/atom, and the mix. None when no mix has the composition."""
    from scipy.optimize import linprog

    composition = energies[target][0].fractional_composition
    elements = sorted(composition.elements, key=str)
    pool = [k for k in pool if set(energies[k][0].elements) <= set(elements)]
    if not pool:
        return None
    a = np.array([[energies[k][0].fractional_composition[e] for k in pool] for e in elements])
    b = np.array([composition[e] for e in elements])
    solved = linprog([energies[k][1] for k in pool], A_eq=a, b_eq=b, bounds=[(0, None)] * len(pool), method="highs")
    if solved.status != 0:
        return None
    mix = {k: round(float(x), 4) for k, x in zip(pool, solved.x) if x > 1e-6}
    return float(energies[target][1] - solved.fun) * 1000, mix


def balance(args) -> None:
    out = Path(args.out)
    planned, state, _ = load(out)
    roles = {c["label"]: c["role"] for c in planned["cells"]}
    formulas = {c["label"]: c["formula"] for c in planned["cells"]}
    results = []
    for kind in ("scf", "relax"):
        energies = {}
        for label, entry in state.items():
            run = entry.get(kind) or {}
            result = run.get("result") or {}
            energy = result.get("final_energy_ev", result.get("total_energy_ev"))
            if run.get("status") == "success" and energy is not None and result.get("n_atoms"):
                energies[label] = (Composition(formulas[label]), energy / result["n_atoms"])
        missing = [label for label in roles if label not in energies]
        if not energies:
            continue
        print(f"\n{'Single points' if kind == 'scf' else 'Relaxed'} ({len(energies)} of {len(roles)} cells)")
        if missing:
            print(f"  no energy yet for {', '.join(missing)}: a balance without them is an upper bound on stability.")
        for target in (label for label, role in roles.items() if role == "target" and label in energies):
            others = [k for k in energies if k != target]
            for tag, pool in (
                ("compound-only", [k for k in others if roles[k] != "element"]),
                ("with elements (uncalibrated)", others),
            ):
                found = lowest_combination(energies, target, pool)
                if found is None:
                    print(f"  {target} [{tag}]: no combination of the computed phases has this composition")
                    continue
                if tag.startswith("with") and not any(roles[k] == "element" for k in found[1]):
                    continue  # same answer as compound-only
                print(f"  {target} [{tag}]: {found[0]:+.1f} meV/atom against {found[1]}")
                results.append(dict(target=target, kind=kind, balance=tag, meV_per_atom=round(found[0], 1), mix=found[1]))
    (out / "balance.json").write_text(json.dumps(results, indent=1))
    print("\nUnder about 5 meV/atom either way is 'on the hull', not 'below it'. Only phases computed here compete.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("targets", nargs="+", help="relaxed CIFs of the candidates")
    p.add_argument("--add", action="append", help="label=cell.cif, a competitor no database lists")
    p.add_argument("--published", action="store_true", help="competitors from published sources only")
    p.add_argument(
        "--basis", choices=("DZP", "TZDP"), default=SETTINGS["basis_size"],
        help="basis for every phase of the balance (default DZP; TZDP needs the user's word)",
    )
    s = sub.add_parser("submit")
    s.add_argument("--campaign", required=True)
    s.add_argument("--relax", action="store_true", help="variable-cell relaxations instead of single points")
    s.add_argument("--yes", action="store_true", help="really start the DFT runs")
    c = sub.add_parser("collect")
    c.add_argument("--refresh", action="store_true", help="read finished runs again (their seeds and site moments)")
    b = sub.add_parser("balance")
    for command in (p, s, c, b):
        command.add_argument("--out", required=True, help="the experiment's work folder")
    args = parser.parse_args()
    {"plan": plan, "submit": submit, "collect": collect, "balance": balance}[args.cmd](args)


if __name__ == "__main__":
    main()
