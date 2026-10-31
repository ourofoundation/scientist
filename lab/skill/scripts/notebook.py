#!/usr/bin/env python3
"""Lab notebook helper for the scientist skill. Standard library only.

    notebook.py init                       create the notebook and its git repo
    notebook.py new <slug> "<title>"       start a campaign folder
    notebook.py open                       experiments still running, all campaigns
    notebook.py calibration <slug>         how often predictions held
    notebook.py spend <slug>               dollars spent, expensive runs used

The notebook is lab/notebook/ unless SCIENTIST_NOTEBOOK points elsewhere.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(
    os.environ.get("SCIENTIST_NOTEBOOK")
    or Path(__file__).resolve().parents[2] / "notebook"
)
CAMPAIGNS = ROOT / "campaigns"
# DFT routes (the "expensive" tier in routes.md).
EXPENSIVE = {
    f"mmoderwell/{name}"
    for name in (
        "magnetic-anisotropy-energy", "ground-state-scf", "magnetic-moments",
        "magnetic-ordering-fm-vs-afm", "exchange-couplings-jij",
        "curie-temperature-tc", "dft-structure-relaxation", "density-of-states",
        "projected-density-of-states", "band-structure", "band-gap",
        "charge-density", "orbitals-at-gamma",
        "estimate-zt-and-key-thermoelectric-properties",
        "predict-seebeck-coefficient-and-band-gap",
        "estimate-minimum-lattice-thermal-conductivity",
    )
}

INDEX = """# Campaigns

| Campaign | Goal | Status | Best so far | Last session |
|---|---|---|---|---|
"""

LEARNINGS = """# Learnings

Lessons that hold beyond one campaign. Format and rules: lab/skill/notebook.md.

## Routes and measurement

## Models and their biases

## Chemistry and physics

## Search tactics
"""

CAMPAIGN = """# {title}

Goal (the user's words):

## Objective
| Criterion | Property | Target | Weight | Route | Tier |
|---|---|---|---|---|---|

Not measurable with our routes:

Constraints:

## Budget
- Spend cap: $ (spent so far: $0)
- Expensive runs:  allowed, 0 used
- Where runs go: org , team , visibility private

## Status
Not started.

## Best so far
None yet.
"""

FILES = {
    "hypotheses.md": "# Hypotheses\n",
    "candidates.md": (
        "# Candidates\n\n"
        "| ID | Formula | Asset | Origin | Novelty | Score | Status |\n"
        "|---|---|---|---|---|---|---|\n"
    ),
    "experiments.jsonl": "",
    "log.md": "# Log\n",
}


def init() -> None:
    CAMPAIGNS.mkdir(parents=True, exist_ok=True)
    for name, text in (("index.md", INDEX), ("learnings.md", LEARNINGS)):
        path = ROOT / name
        if not path.exists():
            path.write_text(text)
    if not (ROOT / ".git").exists():
        subprocess.run(["git", "init", "-q", str(ROOT)], check=True)
    print(f"Notebook ready at {ROOT}")


def new(slug: str, title: str) -> None:
    init()
    folder = CAMPAIGNS / slug
    if folder.exists():
        sys.exit(f"Campaign {slug} already exists: {folder}")
    folder.mkdir(parents=True)
    (folder / "campaign.md").write_text(CAMPAIGN.format(title=title))
    for name, text in FILES.items():
        (folder / name).write_text(text)
    with (ROOT / "index.md").open("a") as f:
        f.write(
            f"| [{slug}](campaigns/{slug}/campaign.md) | {title} | active | none yet "
            f"| {date.today().isoformat()} |\n"
        )
    print(f"Campaign started: {folder}")


def experiments(slug: str) -> dict[str, dict]:
    """Merge the ledger: later lines for an id update earlier ones."""
    path = CAMPAIGNS / slug / "experiments.jsonl"
    if not path.exists():
        sys.exit(f"No ledger at {path}")
    merged: dict[str, dict] = {}
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"  skipping {path.name}:{n}: {e}", file=sys.stderr)
            continue
        merged.setdefault(row["id"], {}).update(row)
    return merged


def open_runs() -> None:
    found = False
    for folder in sorted(CAMPAIGNS.glob("*")):
        if not (folder / "experiments.jsonl").exists():
            continue
        for eid, e in experiments(folder.name).items():
            if e.get("status") == "running":
                found = True
                print(
                    f"{folder.name}  {eid}  {e.get('route', e.get('kind'))}  "
                    f"action={e.get('action_id')}  since {e.get('date')}"
                )
    if not found:
        print("Nothing running.")


def calibration(slug: str) -> None:
    rows = experiments(slug)
    by_prior: dict[str, list[bool]] = defaultdict(list)
    surprise: dict[str, list[float]] = defaultdict(list)
    failed = 0
    for e in rows.values():
        if e.get("status") == "failed":
            failed += 1
        if e.get("status") != "done":
            continue
        prior = e.get("prior", "unmarked")
        if isinstance(e.get("surprise"), (int, float)):
            surprise[prior].append(float(e["surprise"]))
        p, r = e.get("prediction") or {}, e.get("result") or {}
        value = r.get("value")
        if isinstance(value, (int, float)) and "low" in p and "high" in p:
            by_prior[prior].append(p["low"] <= value <= p["high"])

    print(f"{slug}: {len(rows)} experiments, {failed} failed (unknown, not negative)")
    for prior in sorted(set(by_prior) | set(surprise)):
        hits = by_prior.get(prior, [])
        s = surprise.get(prior, [])
        rate = f"{sum(hits)}/{len(hits)} inside predicted range" if hits else "no ranged predictions"
        mean = f", mean surprise {sum(s) / len(s):.2f}" if s else ""
        print(f"  {prior}: {rate}{mean}")


def spend(slug: str) -> None:
    rows = experiments(slug)
    dollars = sum(float(e.get("cost_usd") or 0) for e in rows.values())
    heavy = [eid for eid, e in rows.items() if e.get("route") in EXPENSIVE]
    print(f"{slug}: ${dollars:.2f} spent, {len(heavy)} expensive runs ({', '.join(heavy) or 'none'})")


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit(__doc__)
    cmd, args = argv[0], argv[1:]
    if cmd == "init":
        init()
    elif cmd == "new" and len(args) == 2:
        new(*args)
    elif cmd == "open":
        open_runs()
    elif cmd == "calibration" and len(args) == 1:
        calibration(args[0])
    elif cmd == "spend" and len(args) == 1:
        spend(args[0])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
