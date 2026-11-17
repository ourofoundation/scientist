#!/usr/bin/env python3
"""Ouro's shared crystal database (crystal-db) for the lab.

One database holds Materials Project, Alexandria, GNoME, GGen output and
every structure submitted on Ouro, with PBE and Orb v3 energies; COD's
experimental structures sit beside it. This script reads it: a chemical
system and its subsystems are copied into a local SQLite file (seconds), and
hulls, competitors, novelty and the false-positive checks run on the copy.
Relaxing and scoring a new cell still needs a GPU, so that goes through the
hull route (screen.py).

    crystal.py hull Fe-Ge-Al [--surface pbe] [--cut 25] [--published]
    crystal.py competitors Fe3Ge2Al            what it would decompose into, on both surfaces
    crystal.py novelty cell.cif [...]          known / new polymorph / new composition
    crystal.py classify relaxed/ [--screen state.json] [--cut 25] [--out table.json]
    crystal.py prototypes build                index of near-hull published structures (once)
    crystal.py prototypes find --contains Co,Ni --nelements 3,4 --max-atoms 24 [--made] [--cifs dir]

Copies are kept in ~/.scientist/crystal/ for six hours; --refresh fetches
again. Our own screens add references all the time, so refresh before a
number you will report.

GNoME and MP's BY-NC records are included (research use). Anything derived
from them keeps that license.

Run with the materials pyenv: ~/.pyenv/versions/materials/bin/python.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sqlite3
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
from pymatgen.analysis.phase_diagram import PhaseDiagram
from pymatgen.analysis.structure_matcher import StructureMatcher
from pymatgen.core import Composition, Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

from crystaldb import ORB_V3, PBE, StructureDatabase
from crystaldb.cod import CodDataset
from crystaldb.database import decompress_cif
from crystaldb.ouro import OuroSharedDatabase
from crystaldb.sources import PUBLISHED_SOURCES, is_published

import labtools

# The shared datasets (all / materials-science, private). The source of truth
# is materials/apps/_shared/structure_db.py; CRYSTALDB_DATASETS overrides.
DATASETS = {
    "structures": "01a14a6e-6b4c-7066-810d-dfd1db1c5437",
    "geometries": "01a14a6e-7719-7f7c-b9b1-78ee11c2cef3",
    "calculations": "01a14a6e-9b53-790f-82fc-52b0de679f77",
    "runs": "01a14a6e-ab20-7057-b4f3-b5806b8cf992",
    "run_structures": "01a14a6e-c6d3-70a2-a65f-d86f83618d3b",
}
COD_DATASET = os.environ.get("COD_DATASET", "01a15c82-afe9-73cc-b0de-759388e72f42")
CACHE = Path(os.environ.get("SCIENTIST_CRYSTAL_CACHE", "~/.scientist/crystal")).expanduser()
MAX_AGE_HOURS = 6.0
SURFACES = {"orb": ORB_V3.id, "pbe": PBE.id}
TRANSITION_METALS = set(
    "Sc Ti V Cr Mn Fe Co Ni Cu Zn Y Zr Nb Mo Tc Ru Rh Pd Ag Cd Hf Ta W Re Os Ir Pt Au".split()
)


@lru_cache(maxsize=1)
def store() -> OuroSharedDatabase:
    override = os.environ.get("CRYSTALDB_DATASETS")
    datasets = json.loads(override) if override else DATASETS
    return OuroSharedDatabase(labtools.client(), datasets, non_commercial=True)


def chemsys(text: str) -> str:
    """'Fe3Ge2Al', 'Fe-Ge-Al' or a CIF path -> 'Al-Fe-Ge'."""
    if text.endswith(".cif") and Path(text).exists():
        elements = Structure.from_file(text).composition.elements
    elif "-" in text:
        return "-".join(sorted(text.split("-")))
    else:
        elements = Composition(text).elements
    return "-".join(sorted(e.symbol for e in elements))


_PULLED: set[str] = set()


def pull(system: str, refresh: bool = False) -> Path:
    """A local copy of a system and all its subsystems. Returns its path.

    A system is fetched at most once per process: replacing the file under a
    connection that is still open breaks that connection."""
    system = chemsys(system)
    path = CACHE / f"{system}.db"
    fresh = path.exists() and time.time() - path.stat().st_mtime < MAX_AGE_HOURS * 3600
    if system in _PULLED or (fresh and not refresh):
        return path
    _PULLED.add(system)
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    with store().reader(systems=[system], runs=False) as db:
        dest = sqlite3.connect(tmp)
        db.conn.backup(dest)
        # The copy arrives in WAL mode, which a read-only open cannot use.
        dest.execute("PRAGMA journal_mode=DELETE")
        dest.close()
    os.replace(tmp, path)
    return path


def database(system: str, surface: str = "orb", refresh: bool = False) -> StructureDatabase:
    # snapshot: a refresh replaces the file, it never changes one that is open.
    return StructureDatabase(pull(system, refresh), surface=SURFACES[surface], readonly=True, snapshot=True)


def age(system: str) -> str:
    hours = (time.time() - (CACHE / f"{chemsys(system)}.db").stat().st_mtime) / 3600
    return f"copy is {hours:.1f} h old" if hours >= 0.1 else "copy is fresh"


# -------------------- Hulls and competitors --------------------


def entries(db: StructureDatabase, system: str, published: bool = False, drop=lambda row: False):
    """Hull entries on the database's surface, each with its row in .data."""
    out = []
    for row in db.hull_rows(system):
        if row["correction_per_atom"] is None or drop(row):
            continue
        if published and not is_published(row["source"]):
            continue
        entry = db.hull_entry(row)
        entry.data["formula"] = row["reduced_formula"]
        out.append(entry)
    return out


def experimental(db: StructureDatabase, structure_id: str) -> bool:
    row = db.conn.execute(
        "SELECT json_extract(metadata, '$.experimental') FROM structures WHERE id = ?", (structure_id,)
    ).fetchone()
    return bool(row and row[0])


def hull(system: str, surface: str = "orb", cut: float = 25.0, published: bool = False, refresh: bool = False):
    """Every phase within `cut` meV/atom of the hull, lowest first."""
    system = chemsys(system)
    db = database(system, surface, refresh)
    found = entries(db, system, published)
    diagram = PhaseDiagram(found)
    rows = []
    for entry in found:
        distance = diagram.get_e_above_hull(entry) * 1000
        if distance <= cut:
            rows.append(
                dict(
                    formula=entry.data["formula"],
                    space_group=entry.data["space_group"],
                    source=entry.data["source"],
                    source_id=entry.data["source_id"],
                    e_hull_meV=round(distance, 1),
                    made=experimental(db, entry.data["structure_id"]),
                    structure_id=entry.data["structure_id"],
                )
            )
    return sorted(rows, key=lambda r: (r["e_hull_meV"], r["formula"]))


def competitors(target: str, published: bool = False, refresh: bool = False) -> dict:
    """What a composition decomposes into on each surface when no entry of
    its own formula made on Ouro is on the hull. A published entry with the
    same formula stays: a known polymorph is a competitor. When that entry is
    the whole answer (the target is itself in a database, or a polymorph of
    it is on the hull), it is listed first and the decomposition without the
    formula follows, since that is what a PBE check has to compute.

    The two surfaces name different phases often enough that a PBE check
    needs the union (learnings L199)."""
    comp = (
        Structure.from_file(target).composition if target.endswith(".cif") else Composition(target)
    )
    system = "-".join(sorted(e.symbol for e in comp.elements))
    own = comp.reduced_formula
    out = {}
    for name in SURFACES:
        db = database(system, name, refresh)
        found = entries(
            db,
            system,
            published,
            drop=lambda row: row["reduced_formula"] == own and not is_published(row["source"]),
        )

        def rows(entries_):
            decomposition = PhaseDiagram(entries_).get_decomposition(comp)
            return sorted(
                (
                    dict(
                        formula=e.data["formula"],
                        fraction=round(amount, 4),
                        space_group=e.data["space_group"],
                        source=e.data["source"],
                        source_id=e.data["source_id"],
                        structure_id=e.data["structure_id"],
                    )
                    for e, amount in decomposition.items()
                ),
                key=lambda r: -r["fraction"],
            )

        out[name] = rows(found)
        if [r["formula"] for r in out[name]] == [own]:
            out[name] += rows([e for e in found if e.data["formula"] != own])
    return out


def cif_of(db: StructureDatabase, structure_id: str) -> str | None:
    """A stored structure's CIF: its published geometry when it has one."""
    return db.get_geometry(structure_id, "source") or db.get_geometry(structure_id)


# -------------------- Novelty --------------------


@lru_cache(maxsize=1)
def cod() -> CodDataset:
    return CodDataset(labtools.client(), COD_DATASET)


@lru_cache(maxsize=None)
def novelty_index(system: str, refresh: bool = False):
    return database(system, "orb", refresh).novelty_index(extra=[cod().candidates])


def novelty(structure: Structure, refresh: bool = False) -> dict:
    result = novelty_index(chemsys(structure.composition.reduced_formula), refresh).check(structure)
    return dict(
        status=result.status,
        made=result.experimental,
        matches=[
            dict(source=m.source, source_id=m.source_id, sg=m.space_group_number, made=bool(m.experimental))
            for m in result.matches[:4]
        ],
        ours=len(result.ouro_matches),
        same_formula=result.same_formula_counts,
    )


# -------------------- False-positive checks --------------------


def parent_atoms(structure: Structure) -> int:
    """Atoms in the primitive cell once every species is one element. Under 6
    means the cell is an ordering on a simple lattice (bcc, fcc, hcp): an
    ordered alloy, not a structure type of its own (learnings L193)."""
    one = structure.copy()
    one.replace_species({e: "Fe" for e in structure.composition.elements})
    return len(SpacegroupAnalyzer(one, symprec=0.3).get_primitive_standard_structure())


def lattice_occupancy(structure: Structure, tol: float = 0.15) -> tuple[int, int] | None:
    """(atoms, lattice points) when every atom sits on one Bravais lattice
    finer than the cell, else None. 15 of 16 means a simple lattice with one
    site empty: a vacancy-ordered decoration, which the all-one-species
    parent check cannot see (learnings L204)."""
    coords = structure.cart_coords
    lattice = structure.lattice
    nearest = min(n.nn_distance for n in structure.get_neighbors(structure[0], 6.0)) if len(structure) else 0
    if not nearest:
        return None
    vectors = [n.coords - coords[0] for n in structure.get_neighbors(structure[0], nearest * 1.25)]
    best = None
    for triple in itertools.combinations(vectors, 3):
        volume = abs(np.linalg.det(np.array(triple)))
        if volume > 0.2 * nearest**3 and (best is None or volume < best[0] - 1e-6):
            best = (volume, np.array(triple))
    if best is None:
        return None
    volume, basis = best
    inverse = np.linalg.inv(basis)

    def on_lattice(points) -> bool:
        fractional = points @ inverse
        return bool(np.all(np.abs(fractional - np.round(fractional)) < tol))

    if not on_lattice(lattice.matrix) or not on_lattice(coords - coords[0]):
        return None
    points = int(round(lattice.volume / volume))
    return (len(structure), points) if points >= len(structure) else None


class Orderings:
    """Is a cell a known structure type with two of its elements sharing one
    sublattice? Merge each pair of elements in turn and match the result
    against every published entry of the system and its subsystems with the
    same two elements merged (learnings L196, L204).

    A hit is a fact about the cell, not a verdict. Two transition metals on
    one sublattice of a known type is most likely a solid solution; a
    metalloid ordered onto one site of a binary can be a compound in its own
    right (Fe3Ge2Al on CoSn-type FeGe)."""

    def __init__(self, system: str, refresh: bool = False):
        self.db = database(system, "pbe", refresh)
        self.rows = self.db.conn.execute(
            f"""
            SELECT s.id, s.source, s.source_id, s.reduced_formula, s.elements, s.space_group_symbol,
                   s.num_atoms, c.e_above_hull,
                   COALESCE(
                     (SELECT g.cif FROM geometries g WHERE g.structure_id = s.id AND g.kind = 'source'),
                     (SELECT g.cif FROM geometries g WHERE g.structure_id = s.id ORDER BY g.kind LIMIT 1)
                   ) AS cif
            FROM structures s
            LEFT JOIN calculations c ON c.structure_id = s.id AND c.surface = ?
            WHERE s.is_valid = 1 AND s.source IN ({",".join("?" * len(PUBLISHED_SOURCES))})
            """,
            [PBE.id, *PUBLISHED_SOURCES],
        ).fetchall()
        self.matcher = StructureMatcher(ltol=0.2, stol=0.3, angle_tol=5, attempt_supercell=True)
        self._parsed: dict[str, Structure | None] = {}

    def _structure(self, row) -> Structure | None:
        if row["id"] not in self._parsed:
            try:
                self._parsed[row["id"]] = Structure.from_str(decompress_cif(row["cif"]), fmt="cif")
            except Exception:
                self._parsed[row["id"]] = None
        return self._parsed[row["id"]]

    def find(self, structure: Structure) -> dict | None:
        """The best parent: fewest elements, then lowest on the PBE hull."""
        elements = sorted(e.symbol for e in structure.composition.elements)
        own = structure.composition.reduced_formula
        hits = []
        for keep, gone in itertools.combinations(elements, 2):
            merged = structure.copy()
            merged.replace_species({gone: keep})
            wanted = merged.composition.reduced_formula
            rest = set(elements) - {keep, gone}
            for row in self.rows:
                theirs = set(json.loads(row["elements"]))
                if row["reduced_formula"] == own or theirs - {keep, gone} != rest or not theirs & {keep, gone}:
                    continue
                if row["cif"] is None:
                    continue
                if Composition(row["reduced_formula"]).replace({gone: {keep: 1}}).reduced_formula != wanted:
                    continue
                reference = self._structure(row)
                if reference is None:
                    continue
                reference = reference.copy()
                reference.replace_species({gone: keep})
                if self.matcher.fit(merged, reference):
                    distance = row["e_above_hull"]
                    hits.append(((len(theirs), distance is None, distance or 0.0), row, keep, gone))
        if not hits:
            return None
        _, row, keep, gone = min(hits, key=lambda hit: hit[0])
        return dict(
            parent=row["reduced_formula"],
            parent_sg=row["space_group_symbol"],
            parent_id=f"{row['source']}:{row['source_id']}",
            parent_e_hull_meV=None if row["e_above_hull"] is None else round(row["e_above_hull"] * 1000, 1),
            shared=f"{keep}/{gone}",
            kind="two transition metals" if {keep, gone} <= TRANSITION_METALS else "site substitution",
            parents_matched=len(hits),
        )


@lru_cache(maxsize=None)
def orderings(system: str, refresh: bool = False) -> Orderings:
    return Orderings(system, refresh)


def classify(structure: Structure, refresh: bool = False) -> dict:
    """One relaxed cell through the checks, cheapest first. `label` is the
    first that fires:

      decoration          an ordering on bcc/fcc/hcp
      vacancy decoration  the same with empty sites
      known               matches a published or COD structure
      ordering (metals)   a known type with two transition metals on one sublattice
      ordering (site)     a known type with one site given to another element
      new polymorph       its formula is published, in another structure
      unmatched           none of the above: a candidate for a new type. If its
                          binary parent may be missing from the database, check
                          the type by name before saying so.
    """
    system = chemsys(structure.composition.reduced_formula)
    analyzer = SpacegroupAnalyzer(structure, symprec=0.1)
    out = dict(
        formula=structure.composition.reduced_formula,
        natoms=len(analyzer.get_primitive_standard_structure()),
        sg=analyzer.get_space_group_symbol(),
        sg_number=analyzer.get_space_group_number(),
        parent_atoms=parent_atoms(structure),
    )
    occupancy = lattice_occupancy(structure)
    out["lattice"] = None if occupancy is None else f"{occupancy[0]} of {occupancy[1]} points"
    out["novelty"] = novelty(structure, refresh)
    out["ordering"] = None
    if out["parent_atoms"] < 6:
        out["label"] = "decoration"
    elif occupancy and occupancy[0] / occupancy[1] >= 0.75:
        out["label"] = "vacancy decoration"
    elif out["novelty"]["status"] == "known":
        out["label"] = "known"
    else:
        out["ordering"] = orderings(system, refresh).find(structure)
        if out["ordering"]:
            out["label"] = "ordering (metals)" if out["ordering"]["kind"] == "two transition metals" else "ordering (site)"
        else:
            out["label"] = "new polymorph" if out["novelty"]["status"] == "new polymorph" else "unmatched"
    return out


# -------------------- Prototypes --------------------

PROTOTYPES = CACHE / "prototypes.db"
SHARDS = 256


def build_prototypes(hull_cut: float, max_atoms: int, sg_min: int) -> int:
    """Index every near-hull MP and Alexandria structure (no geometries).
    One query over the whole database times out, so it goes by key range."""
    db = store()
    structures, calculations = db.table("structures"), db.table("calculations")
    bounds = [f"{i:02x}000000-0000-0000-0000-000000000000" for i in range(SHARDS)] + [None]

    def shard(i: int):
        upper = f"AND s.id < '{bounds[i + 1]}'" if bounds[i + 1] else ""
        return db.query(
            f"""
            SELECT s.id, s.source, s.source_id, s.reduced_formula, s.chemsys, s.elements, s.num_atoms,
                   s.space_group_number, s.space_group_symbol, c.e_above_hull,
                   s.metadata->>'experimental' AS experimental
            FROM {structures} s JOIN {calculations} c ON c.structure_id = s.id AND c.surface = 'pbe'
            WHERE s.id >= '{bounds[i]}' {upper} AND s.is_valid AND s.source IN ('mp', 'alexandria')
              AND s.space_group_number >= {sg_min} AND s.num_atoms <= {max_atoms}
              AND c.e_above_hull <= {hull_cut / 1000}
            """
        )

    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = PROTOTYPES.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    conn.execute(
        "CREATE TABLE prototypes (id TEXT PRIMARY KEY, source TEXT, source_id TEXT, reduced_formula TEXT, "
        "chemsys TEXT, elements TEXT, nelements INT, num_atoms INT, sg INT, sg_symbol TEXT, e_hull REAL, made INT)"
    )
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    total = 0
    with ThreadPoolExecutor(8) as pool:
        for rows in pool.map(shard, range(SHARDS)):
            conn.executemany(
                "INSERT OR REPLACE INTO prototypes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        r["id"], r["source"], r["source_id"], r["reduced_formula"], r["chemsys"],
                        json.dumps(r["elements"]), len(r["elements"]), r["num_atoms"], r["space_group_number"],
                        r["space_group_symbol"], r["e_above_hull"], int(str(r["experimental"]).lower() == "true"),
                    )
                    for r in rows
                ],
            )
            total += len(rows)
    meta = dict(built=time.strftime("%Y-%m-%d %H:%M"), hull_cut_meV=hull_cut, max_atoms=max_atoms, sg_min=sg_min)
    conn.executemany("INSERT INTO meta VALUES (?,?)", [(k, str(v)) for k, v in meta.items()])
    conn.commit()
    conn.close()
    os.replace(tmp, PROTOTYPES)
    return total


def in_cod(formulas: list[str]) -> set[str]:
    """Which reduced formulas have an entry in COD, i.e. were reported as made."""
    found: set[str] = set()
    for start in range(0, len(formulas), 400):
        chunk = ", ".join("'" + f.replace("'", "''") + "'" for f in formulas[start : start + 400])
        rows = cod().query(f"SELECT DISTINCT reduced_formula FROM {cod().table} WHERE reduced_formula IN ({chunk})")
        found.update(r["reduced_formula"] for r in rows)
    return found


def find_prototypes(contains=(), nelements=(), max_atoms=None, hull_cut=None, sg=(75, 230), made=False) -> list[dict]:
    """Near-hull published structures to substitute into.

    `made` keeps those reported as synthesized: MP's experimental flag, or a
    COD entry with the same formula. Every true-type hit in sun-tetragonal
    sat on a type with a made member (learnings L200)."""
    if not PROTOTYPES.exists():
        sys.exit("No prototype index yet: run `crystal.py prototypes build`.")
    conn = sqlite3.connect(PROTOTYPES)
    conn.row_factory = sqlite3.Row
    where, params = ["sg BETWEEN ? AND ?"], list(sg)
    if nelements:
        where.append(f"nelements IN ({','.join('?' * len(nelements))})")
        params += list(nelements)
    if max_atoms:
        where.append("num_atoms <= ?")
        params.append(max_atoms)
    if hull_cut is not None:
        where.append("e_hull <= ?")
        params.append(hull_cut / 1000)
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM prototypes WHERE {' AND '.join(where)}", params)]
    if contains:
        rows = [r for r in rows if set(json.loads(r["elements"])) & set(contains)]
    if made:
        reported = in_cod(sorted({r["reduced_formula"] for r in rows if not r["made"]}))
        rows = [r for r in rows if r["made"] or r["reduced_formula"] in reported]
    return rows


def fetch_cifs(structure_ids: list[str], out: Path, names: dict[str, str]) -> int:
    """Write the published geometry of each structure to out/<name>.cif."""
    out.mkdir(parents=True, exist_ok=True)
    written = 0
    for start in range(0, len(structure_ids), 2000):
        with store().reader(structure_ids=structure_ids[start : start + 2000], runs=False) as db:
            for structure_id in structure_ids[start : start + 2000]:
                cif = cif_of(db, structure_id)
                if cif:
                    (out / f"{names[structure_id]}.cif").write_text(cif)
                    written += 1
    return written


# -------------------- Command line --------------------


def table(rows: list[dict], columns: list[str]) -> None:
    if not rows:
        print("(nothing)")
        return
    widths = [max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns]
    print("  ".join(c.ljust(w) for c, w in zip(columns, widths)))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(w) for c, w in zip(columns, widths)))


def cif_paths(args: list[str]) -> list[Path]:
    paths: list[Path] = []
    for arg in args:
        p = Path(arg)
        paths += sorted(p.glob("*.cif")) if p.is_dir() else [p]
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("hull")
    p.add_argument("system")
    p.add_argument("--surface", choices=SURFACES, default="orb")
    p.add_argument("--cut", type=float, default=25.0, help="meV/atom")
    p.add_argument("--published", action="store_true", help="leave out GGen output and Ouro submissions")
    p.add_argument("--refresh", action="store_true")

    p = sub.add_parser("competitors")
    p.add_argument("target", help="a formula or a CIF")
    p.add_argument("--published", action="store_true")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--out", help="write the result as JSON")

    p = sub.add_parser("novelty")
    p.add_argument("cifs", nargs="+")
    p.add_argument("--refresh", action="store_true")

    p = sub.add_parser("classify")
    p.add_argument("cifs", nargs="+", help="relaxed CIFs or folders of them")
    p.add_argument("--screen", help="a screen.py state.json, to add hull distances and filter by --cut")
    p.add_argument("--cut", type=float, default=None, help="meV/atom; needs --screen")
    p.add_argument("--out", help="write the full table as JSON")
    p.add_argument("--refresh", action="store_true")

    p = sub.add_parser("prototypes")
    psub = p.add_subparsers(dest="what", required=True)
    b = psub.add_parser("build")
    b.add_argument("--hull", type=float, default=25.0, help="meV/atom on the published PBE hull")
    b.add_argument("--max-atoms", type=int, default=40)
    b.add_argument("--sg-min", type=int, default=75)
    f = psub.add_parser("find")
    f.add_argument("--contains", default="", help="any of these elements, comma-separated")
    f.add_argument("--nelements", default="", help="e.g. 3,4")
    f.add_argument("--max-atoms", type=int)
    f.add_argument("--hull", type=float, help="meV/atom")
    f.add_argument("--sg", default="75,230", help="space group range")
    f.add_argument("--made", action="store_true", help="only formulas reported as synthesized")
    f.add_argument("--out", help="write the rows as JSON")
    f.add_argument("--cifs", help="fetch the CIFs into this folder")

    args = parser.parse_args()

    if args.cmd == "hull":
        rows = hull(args.system, args.surface, args.cut, args.published, args.refresh)
        print(f"{chemsys(args.system)} on {args.surface}, within {args.cut:g} meV/atom ({age(args.system)}):")
        table(rows, ["formula", "space_group", "e_hull_meV", "source", "source_id", "made"])

    elif args.cmd == "competitors":
        found = competitors(args.target, args.published, args.refresh)
        for surface, rows in found.items():
            print(f"\n{surface} hull without the target:")
            table(rows, ["formula", "fraction", "space_group", "source", "source_id"])
        union = sorted({r["formula"] for rows in found.values() for r in rows})
        print(f"\nPBE check set (union): {', '.join(union)}")
        print("Add by hand any competitor no database lists (a filled or emptied variant of a neighbour).")
        if args.out:
            Path(args.out).write_text(json.dumps(found, indent=1))

    elif args.cmd == "novelty":
        for path in cif_paths(args.cifs):
            r = novelty(Structure.from_file(path), args.refresh)
            matches = ", ".join(f"{m['source']}:{m['source_id']}" + (" (made)" if m["made"] else "") for m in r["matches"])
            print(f"{path.name}: {r['status']}" + (f" [{matches}]" if matches else "") + f"; same formula: {r['same_formula'] or 'none'}")

    elif args.cmd == "classify":
        screen = json.loads(Path(args.screen).read_text()) if args.screen else {}
        rows = []
        for path in cif_paths(args.cifs):
            name = path.stem
            distance = ((screen.get(name) or {}).get("result") or {}).get("e_above_hull")
            if args.cut is not None and (distance is None or distance * 1000 > args.cut):
                continue
            try:
                row = classify(Structure.from_file(path), args.refresh)
            except Exception as error:  # one bad cell must not stop a batch
                row = dict(label="error", error=repr(error)[:200])
            row.update(name=name, e_hull_meV=None if distance is None else round(distance * 1000, 1))
            rows.append(row)
        rows.sort(key=lambda r: (r["e_hull_meV"] is None, r["e_hull_meV"] or 0))
        for r in rows:
            o = r.get("ordering")
            r["detail"] = (
                f"{o['parent']} {o['parent_sg']} ({o['parent_id']}, {o['parent_e_hull_meV']} meV), {o['shared']} share a sublattice" if o
                else r.get("lattice") if r.get("label") == "vacancy decoration"
                else ", ".join(f"{m['source']}:{m['source_id']}" for m in (r.get("novelty") or {}).get("matches", [])) if r.get("label") == "known"
                else r.get("error", "")
            )
        table(rows, ["e_hull_meV", "name", "formula", "sg", "natoms", "label", "detail"])
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["label"]] = counts.get(r["label"], 0) + 1
        print("\n" + ", ".join(f"{n} {label}" for label, n in sorted(counts.items(), key=lambda kv: -kv[1])))
        if args.out:
            Path(args.out).write_text(json.dumps(rows, indent=1))

    elif args.what == "build":
        start = time.time()
        total = build_prototypes(args.hull, args.max_atoms, args.sg_min)
        print(f"{total} prototypes indexed in {time.time() - start:.0f} s -> {PROTOTYPES}")

    else:
        rows = find_prototypes(
            contains=[e for e in args.contains.split(",") if e],
            nelements=[int(n) for n in args.nelements.split(",") if n],
            max_atoms=args.max_atoms,
            hull_cut=args.hull,
            sg=tuple(int(n) for n in args.sg.split(",")),
            made=args.made,
        )
        by_source: dict[str, int] = {}
        for r in rows:
            by_source[r["source"]] = by_source.get(r["source"], 0) + 1
        print(f"{len(rows)} prototypes ({', '.join(f'{n} {s}' for s, n in by_source.items())})")
        if args.out:
            Path(args.out).write_text(json.dumps(rows, indent=0))
        if args.cifs:
            names = {r["id"]: f"{r['reduced_formula']}_sg{r['sg']}_{r['source_id']}".replace("/", "_") for r in rows}
            written = fetch_cifs(list(names), Path(args.cifs), names)
            print(f"{written} CIFs in {args.cifs}")


if __name__ == "__main__":
    main()
