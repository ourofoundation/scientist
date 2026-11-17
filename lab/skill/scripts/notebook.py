#!/usr/bin/env python3
"""Lab notebook helper for the scientist skill. Standard library only.

    notebook.py init                       create the notebook and its git repo
    notebook.py new <slug> "<title>"       start a campaign folder

  Reading (the notebook is too large to read whole):
    notebook.py brief <slug>               what a session reads first: the state page, open
                                           hypotheses, live candidates, the last log entry, in
                                           flight, and the instrument register
    notebook.py learnings                  every lesson, one line each
    notebook.py recall <term|id>...        everything the notebook holds on a topic: lessons in
                                           full, then instruments, hypotheses, candidates and log
                                           entries. Words must all match (one topic per call);
                                           ids alone (L177 H145) each match on their own.
                                           --campaign <slug>, --limit <n>
    notebook.py check [<slug>]             ids used twice, state pages over their size, stale
                                           runs, and anything called met that the claim ladder
                                           in campaign.md does not carry. Writes nothing
    notebook.py open                       experiments still running, all campaigns
    notebook.py calibration <slug>         how often predictions held
    notebook.py spend <slug> [--ids]       dollars spent, expensive runs used

  Writing:
    notebook.py next-id <slug> E|H|C       reserve the next experiment, hypothesis or candidate id
    notebook.py next-id L                  reserve the next learning id
    notebook.py index <slug> <status> "<best so far>"   rewrite the campaign's row in index.md

The notebook is lab/notebook/ unless SCIENTIST_NOTEBOOK points elsewhere.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
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

INSTRUMENTS = """# Instruments

What each route has been shown to do, one structure family at a time. Format
and rules: lab/skill/notebook.md. No row for a route in a family means it is
uncalibrated there.

| Route | Family | Protocol | Reference | Route reads | Trust | Evidence |
|---|---|---|---|---|---|---|
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

| Candidate | Rung | Instrument | Stability | Settings | Arrangement | Makeability | Novelty |
|---|---|---|---|---|---|---|---|

## Map
| Family | State | Best cell and what it reached | What closed or holds it | Loops | Lessons |
|---|---|---|---|---|---|

## Levers
| Lever | What it did | Works in | Fails in, or what limits it | Untried where it could apply | Lessons |
|---|---|---|---|---|---|
"""

# State pages are rewritten in place and stay small enough to read every
# session. History belongs in log.md, which is read by entry.
CAMPAIGN_BUDGET = 28_000  # characters of campaign.md
BEST_BUDGET = 400  # characters of an index row's "Best so far"
STALE_DAYS = 2  # a run still "running" after this long was probably never collected
EXPERIMENT_ID = re.compile(r"E\d+[a-z]?")

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
    for name, text in (("index.md", INDEX), ("learnings.md", LEARNINGS), ("instruments.md", INSTRUMENTS)):
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


def ledger(slug: str) -> list[dict]:
    """Every line of a campaign's ledger, in order."""
    path = CAMPAIGNS / slug / "experiments.jsonl"
    if not path.exists():
        sys.exit(f"No ledger at {path}")
    rows = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            print(f"  skipping {path.name}:{n}: {e}", file=sys.stderr)
    return rows


def experiments(slug: str) -> dict[str, dict]:
    """Merge the ledger: later lines for an id update earlier ones."""
    merged: dict[str, dict] = {}
    for row in ledger(slug):
        # A malformed id (an action id fused onto it by a shell loop) is not an
        # experiment; `check` lists those lines.
        if row.get("status") != "reserved" and EXPERIMENT_ID.fullmatch(str(row.get("id"))):
            merged.setdefault(row["id"], {}).update(row)
    return merged


def malformed(slug: str) -> list[str]:
    return sorted({str(r.get("id")) for r in ledger(slug) if not EXPERIMENT_ID.fullmatch(str(r.get("id")))})


def stale(slug: str) -> dict[str, str]:
    """Experiments left planned or running that need attention, with what to
    do about each: {id: advice}."""
    out = {}
    for eid, e in experiments(slug).items():
        if e.get("status") not in ("planned", "running"):
            continue
        try:
            age = (date.today() - date.fromisoformat(str(e.get("date")))).days
        except ValueError:
            age = None
        if e.get("result"):
            out[eid] = "has a result on a `running` line: append its `done` line"
        elif age is None or age > STALE_DAYS:
            where = "collect it" if e.get("action_id") else f"no action id: a batch keeps its ids in work/{eid.lower()}/"
            out[eid] = f"open for more than {STALE_DAYS} days: {where}, or record why it is left"
    return out


def reused(slug: str) -> dict[str, list[dict]]:
    """Ids that two experiments share, with each one's finished lines.

    Two sessions working at once (a fork) have both taken the next free id
    before. A line that names a `kind` opens an experiment; later lines for
    the id (running, done, a correction) leave it out. So an id is shared
    when two of its lines open different experiments."""
    lines: dict[str, list[dict]] = defaultdict(list)
    for row in ledger(slug):
        lines[row["id"]].append(row)
    out = {}
    for eid, rows in lines.items():
        opened = {(r.get("kind"), r.get("route"), r.get("hypothesis")) for r in rows if r.get("kind")}
        if len(opened) > 1:
            out[eid] = [r for r in rows if r.get("status") in ("done", "failed")]
    return out


def open_runs() -> None:
    found = False
    for folder in sorted(CAMPAIGNS.glob("*")):
        if not (folder / "experiments.jsonl").exists():
            continue
        old = stale(folder.name)
        for eid, e in experiments(folder.name).items():
            if e.get("status") in ("planned", "running"):
                found = True
                print(
                    f"{folder.name}  {eid}  {e['status']}  {e.get('route', e.get('kind'))}  "
                    f"action={e.get('action_id')}  since {e.get('date')}"
                    + (f"  STALE, {old[eid]}" if eid in old else "")
                )
    if not found:
        print("Nothing planned or running.")


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
        low, high = p.get("low"), p.get("high")
        if isinstance(value, (int, float)) and (low is not None or high is not None):
            # A missing bound is open: {"low": 1.0, "high": null} means "at least 1.0".
            inside = (low is None or value >= low) and (high is None or value <= high)
            by_prior[prior].append(inside)

    print(f"{slug}: {len(rows)} experiments, {failed} failed (unknown, not negative)")
    for prior in sorted(set(by_prior) | set(surprise)):
        hits = by_prior.get(prior, [])
        s = surprise.get(prior, [])
        rate = f"{sum(hits)}/{len(hits)} inside predicted range" if hits else "no ranged predictions"
        mean = f", mean surprise {sum(s) / len(s):.2f}" if s else ""
        print(f"  {prior}: {rate}{mean}")


def spend(slug: str, ids: bool = False) -> None:
    rows = experiments(slug)
    shared = reused(slug)
    # A shared id is two experiments: count each one's cost, not the last line's.
    costs = [
        (e, float(e.get("cost_usd") or 0))
        for eid, e in rows.items()
        if eid not in shared
    ] + [(line, float(line.get("cost_usd") or 0)) for lines in shared.values() for line in lines]
    dollars = sum(cost for _, cost in costs)
    estimated = sum(cost for e, cost in costs if e.get("cost_estimated"))
    heavy = [eid for eid, e in rows.items() if e.get("route") in EXPENSIVE]
    note = f" (${estimated:.2f} of it estimated)" if estimated else ""
    print(f"{slug}: ${dollars:.2f} spent{note}, {len(heavy)} expensive runs")
    if ids and heavy:
        print(f"  {', '.join(heavy)}")
    if shared:
        print(
            f"  {len(shared)} ids are shared by two experiments ({', '.join(sorted(shared))}); "
            "both are counted. See `notebook.py check`."
        )


MARKDOWN_IDS = {
    "H": ("hypotheses.md", re.compile(r"^## H(\d+)\b", re.M)),
    "C": ("candidates.md", re.compile(r"^\| C(\d+) \|", re.M)),
}
LEARNING_ID = re.compile(r"^- L(\d+)\b", re.M)


def reserve(key: str, highest_written: int) -> int:
    """The next number for `key`, recorded in .reserved.json under a file
    lock. A session that takes an id and writes the entry minutes later
    cannot collide with one working beside it."""
    with (ROOT / ".reserved.json").open("a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        text = f.read()
        taken = json.loads(text) if text.strip() else {}
        number = max(highest_written, taken.get(key, 0)) + 1
        taken[key] = number
        f.seek(0)
        f.truncate()
        f.write(json.dumps(taken, indent=1, sort_keys=True) + "\n")
    return number


def next_id(args: list[str]) -> None:
    """Print the next free id and reserve it, so two sessions never get the
    same one. Experiment ids are reserved in the ledger itself."""
    if args == ["L"]:
        numbers = [int(n) for n in LEARNING_ID.findall((ROOT / "learnings.md").read_text())]
        print(f"L{reserve('L', max(numbers, default=0)):03d}")
        return
    if len(args) != 2 or args[1] not in ("E", "H", "C"):
        sys.exit("usage: notebook.py next-id <slug> E|H|C   or   notebook.py next-id L")
    slug, kind = args
    folder = CAMPAIGNS / slug
    if kind != "E":
        name, pattern = MARKDOWN_IDS[kind]
        numbers = [int(n) for n in pattern.findall((folder / name).read_text())]
        print(f"{kind}{reserve(f'{slug}/{kind}', max(numbers, default=0))}")
        return
    with (folder / "experiments.jsonl").open("a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        numbers = [
            int(m.group(1))
            for line in f
            if (m := re.search(r'"id":\s*"E(\d+)"', line))
        ]
        eid = f"E{max(numbers, default=0) + 1:03d}"
        f.write(json.dumps({"id": eid, "status": "reserved", "date": date.today().isoformat()}) + "\n")
    print(eid)


# ---------------------------------------------------------------------------
# Reading. The notebook outgrew what a session can read whole, so these give
# it in tiers: `brief` is the fixed packet, `learnings` the one-line index,
# `recall` the full text on one topic.

SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
NOT_A_SENTENCE_END = ("e.g.", "i.e.", "vs.", "al.", "cf.", "etc.", "approx.", "Fig.", "no.")
# A candidate is live when its Status starts with one of these (notebook.md).
LIVE = ("lead", "measuring", "measured", "met", "meets", "confirmed")
# The claim ladder (SKILL.md). A candidate's rung is the first word of its
# row in campaign.md's claims table.
RUNGS = ("lead", "measured", "met")
UNDECIDED = ("open", "inconclusive", "untested", "unresolved")


def fold(text: str) -> str:
    """Lower case, with Fe₅Si₃ written Fe5Si3, so either spelling finds the other."""
    return text.translate(SUBSCRIPTS).lower()


def cut(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def lessons() -> list[dict]:
    """Every lesson in learnings.md as {id, section, text}."""
    out: list[dict] = []
    section, current = "", None
    for line in (ROOT / "learnings.md").read_text().splitlines():
        if line.startswith("#"):
            section, current = (line[3:].strip() if line.startswith("## ") else section), None
        elif m := re.match(r"- (L\d+)\b\s*(.*)", line):
            current = {"id": m.group(1), "section": section, "lines": [m.group(2)]}
            out.append(current)
        elif current is not None:
            current["lines"].append(line.strip())
    for lesson in out:
        lesson["text"] = " ".join(x for x in lesson.pop("lines") if x)
    return out


def headline(text: str, width: int = 190) -> str:
    """A lesson's index line: its first sentence, and the lessons that amend it."""
    amended: list[str] = []
    related: list[str] = []
    if text.startswith("["):
        end = text.find("]")
        if end > 0:
            # "[Corrected by L245, ...]" is amended by L245; "[Resolves L006; ...]"
            # is the lesson doing the amending, so L006 is only related.
            note, text = text[:end], text[end + 1 :].strip()
            amended = re.findall(r"\bby (L\d+)", note)
            related = [i for i in re.findall(r"L\d+", note) if i not in amended]
    amended += re.findall(r"Superseded by (L\d+)", text)
    first = text
    for m in re.finditer(r"\.\s+(?=[A-Z\[(])", text):
        if not text[: m.start() + 1].endswith(NOT_A_SENTENCE_END):
            first = text[: m.start() + 1]
            break
    tail = f"  [amended by {', '.join(dict.fromkeys(amended))}]" if amended else ""
    tail += f"  [see {', '.join(dict.fromkeys(related))}]" if related else ""
    return cut(first, width) + tail


def learnings_index() -> None:
    rows = lessons()
    print(
        f"{len(rows)} lessons, one line each: the claim only. Evidence, numbers and limits are in\n"
        "the full text: `notebook.py recall L### ...` or `notebook.py recall <terms>`.\n"
        "A lesson amended by a later one is only half the story without it."
    )
    section = None
    for lesson in rows:
        if lesson["section"] != section:
            section = lesson["section"]
            print(f"\n## {section}")
        confidence = re.search(r"Confidence: (\w+)", lesson["text"])
        mark = {"high": " ", "medium": "~", "low": "?"}.get(confidence.group(1) if confidence else "", " ")
        print(f"{lesson['id']}{mark} {headline(lesson['text'])}")
    print("\n~ medium confidence, ? low confidence.")


def sections(path: Path) -> list[tuple[str, str]]:
    """A markdown file as (heading, body) for each `## ` section; text before
    the first one comes back under an empty heading."""
    out: list[tuple[str, list[str]]] = [("", [])]
    for line in path.read_text().splitlines() if path.exists() else []:
        if line.startswith("## "):
            out.append((line[3:].strip(), []))
        else:
            out[-1][1].append(line)
    return [(heading, "\n".join(body).strip()) for heading, body in out]


def hypotheses(slug: str) -> list[dict]:
    out = []
    for heading, body in sections(CAMPAIGNS / slug / "hypotheses.md"):
        if m := re.match(r"(H\d+)\b.*?(?:\[([^\]]*)\])?\s*$", heading):
            # "[refuted (ground state is antiferromagnetic)]" is status "refuted".
            status = ((m.group(2) or "").split() or ["untagged"])[0].lower()
            out.append({"id": m.group(1), "heading": heading, "status": status, "body": body})
    return out


def candidates(slug: str) -> list[dict]:
    """Rows of candidates.md, each with the header of the table it sits in."""
    out, header = [], ""
    path = CAMPAIGNS / slug / "candidates.md"
    for line in path.read_text().splitlines() if path.exists() else []:
        if line.startswith("| ID"):
            header = line
        elif m := re.match(r"\| (C\d+) \|", line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            out.append({"id": m.group(1), "row": line, "formula": cells[1], "status": cells[-1]})
    return out


def table(text: str, first_columns: tuple[str, ...]) -> list[dict[str, str]]:
    """Rows of the markdown table whose header starts with these columns,
    each as {column: cell}."""
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for line in text.splitlines():
        if not line.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if tuple(cells[: len(first_columns)]) == first_columns:
            header = cells
        elif header and not set(line) <= set("|-: "):
            rows.append(dict(zip(header, cells)))
    return rows


def claims(slug: str) -> list[dict[str, str]]:
    """The claims table of campaign.md: one row per standing candidate, its
    rung, and what each check of the ladder rests on."""
    path = CAMPAIGNS / slug / "campaign.md"
    return table(path.read_text(), ("Candidate", "Rung")) if path.exists() else []


def instruments() -> list[dict[str, str]]:
    path = ROOT / "instruments.md"
    return table(path.read_text(), ("Route", "Family")) if path.exists() else []


def ladder_problems(slug: str) -> list[str]:
    """Where the notebook calls something met that the claims table does not
    carry to the top rung. This is the judge's arithmetic, done by code."""
    out = []
    rows = claims(slug)
    met = []
    for row in rows:
        name = row["Candidate"]
        rung = (row["Rung"].split() or [""])[0].lower()
        if rung not in RUNGS:
            out.append(f"{name}: rung '{row['Rung']}' is not one of {', '.join(RUNGS)}")
        elif rung == "met":
            met.append(name)
            open_checks = [
                column for column, cell in row.items()
                if column not in ("Candidate", "Rung") and (not cell or cell.lower().startswith(("open", "fails", "failed")))
            ]
            if open_checks:
                out.append(f"{name} is on the met rung with {', '.join(open_checks)} open or failed: it is measured, not met")
    for c in candidates(slug):
        word = (c["status"].split() or [""])[0].lower().strip(":;,()")
        if word == "met" and not any(c["id"] in name.split() for name in met):
            out.append(f"candidates.md has {c['id']} as met, and the claims table in campaign.md does not")
    for line in (ROOT / "index.md").read_text().splitlines():
        if line.startswith(f"| [{slug}](") and line.split("|")[3].strip().lower().startswith("met") and not met:
            out.append(
                "index.md says met, and campaign.md's claims table has no candidate on the met rung"
                + ("" if rows else " (it has no claims table)")
            )
    return out


def log_entries(slug: str) -> list[tuple[str, str]]:
    return [(h, b) for h, b in sections(CAMPAIGNS / slug / "log.md") if h]


def next_block(body: str) -> str:
    """The `Next` proposal of one log entry: from its `Next` line to the line
    that starts the following field."""
    lines, keep = [], False
    for line in body.splitlines():
        if re.match(r"Next\b", line):
            keep = True
        elif keep and re.match(r"(In flight|Post|Hunch|Learned|Decided|Mistakes?|Calibration|Addendum|Later)\b", line):
            break
        if keep:
            lines.append(line)
    return "\n".join(lines).strip()


def brief(slug: str) -> None:
    folder = CAMPAIGNS / slug
    if not folder.exists():
        sys.exit(f"No campaign {slug}. Campaigns: {', '.join(sorted(p.name for p in CAMPAIGNS.glob('*')))}")

    page = (folder / "campaign.md").read_text()
    if len(page) <= CAMPAIGN_BUDGET:
        print(page.rstrip())
    else:
        # An overgrown state page: show the top of each section and say so.
        for heading, body in sections(folder / "campaign.md"):
            print(f"## {heading}" if heading else "", body[:3000], sep="\n")
            if len(body) > 3000:
                print(f"[cut: {len(body) - 3000} more characters in campaign.md]")
        print(
            f"\n[campaign.md is {len(page)} characters, over the {CAMPAIGN_BUDGET} a state page gets. "
            "Rewrite Status, Best so far and Map as the current state; the history is in log.md.]"
        )

    hyps = hypotheses(slug)
    undecided = [h for h in hyps if h["status"] in UNDECIDED]
    print(f"\n# Hypotheses: {len(hyps)} written, {sum(h['status'] == 'open' for h in hyps)} open")
    for h in hyps:
        if h["status"] == "open":
            print(f"\n## {h['heading']}\n{h['body'][:2500]}")
            if len(h["body"]) > 2500:
                print(f"[cut: read {h['id']} in hypotheses.md]")
    waiting = [h for h in undecided if h["status"] != "open"]
    if waiting:
        print("\nNot settled (`notebook.py recall H##` for the text):")
        for h in waiting:
            print(f"- {cut(h['heading'], 170)}")
    tally = Counter(h["status"] for h in hyps if h not in undecided)
    if tally:
        print("\nSettled: " + ", ".join(f"{n} {status}" for status, n in tally.most_common()))

    rows = candidates(slug)
    live = [c for c in rows if fold(c["status"]).startswith(LIVE)]
    print(
        f"\n# Candidates: {len(rows)} rows, {len(live)} live "
        "(id, formula, status; `notebook.py recall C##` for the row and its asset id)"
    )
    for c in live:
        print(f"- {c['id']} {cut(c['formula'], 70)}: {cut(c['status'], 220)}")
    print(f"Not shown: {len(rows) - len(live)} dropped, parked, parent, calibration or reference rows.")

    entries = log_entries(slug)
    if entries:
        heading, body = entries[0]
        print(f"\n# Last log entry ({len(entries)} in log.md)\n\n## {heading}\n{body[:14000]}")
        # An option an earlier session ranked second or third is often still
        # open, with the condition it was waiting on; the last entry alone hides it.
        earlier = [(h, next_block(b)) for h, b in entries[1:5]]
        earlier = [(h, block) for h, block in earlier if block][:3]
        if earlier:
            print("\n# What the entries before it proposed (their `Next`, as written then)")
            for h, block in earlier:
                print(f"\n## {cut(h, 150)}\n{block[:2200]}")

    print("\n# In flight")
    old = stale(slug)
    running = {eid: e for eid, e in experiments(slug).items() if e.get("status") in ("planned", "running")}
    for eid, e in running.items():
        print(
            f"- {eid} {e['status']} {e.get('route', e.get('kind'))} action={e.get('action_id')} "
            f"since {e.get('date')}" + (f". STALE, {old[eid]}" if eid in old else "")
        )
    if not running:
        print("Nothing planned or running in the ledger.")
    print()
    spend(slug)
    if problems := ladder_problems(slug):
        print("\n# The claim ladder disagrees with the notebook\n" + "\n".join(f"- {p}" for p in problems))
    register = ROOT / "instruments.md"
    if register.exists():
        print(f"\n# Instruments (lab/notebook/instruments.md, all campaigns)\n\n{register.read_text().split(chr(10), 1)[-1].strip()}")
    print(
        f"\n# Not in this packet\n{len(lessons())} lessons: `notebook.py learnings` for the index. "
        "Before choosing what to do, `notebook.py recall <family, parent compound, route>`."
    )


def recall(args: list[str]) -> None:
    limit, only, terms = 10, None, []
    it = iter(args)
    for a in it:
        if a == "--limit":
            limit = int(next(it))
        elif a == "--campaign":
            only = next(it)
        else:
            terms.append(a)
    if not terms:
        sys.exit("usage: notebook.py recall <term|id>... [--campaign <slug>] [--limit <n>]")
    terms = [t.upper() if re.fullmatch(r"[lhec]\d+[a-z]?", t) else t for t in terms]

    is_id = [bool(re.fullmatch(r"[LHEC]\d+[a-z]?", t)) for t in terms]

    def pattern(term: str, an_id: bool):
        """What a term matches, and in which form of the text.
        - An id (L177, C324) matches as a whole word, so L17 does not find L177.
        - A formula (Fe3C, Fe5Si3) matches case-sensitively and must end where
          an element ends, so Fe3C does not find Fe3Co or Fe3C2.
        - Anything else is a case-insensitive substring."""
        if an_id:
            return re.compile(rf"\b{re.escape(term.upper())}\b"), False
        if re.fullmatch(r"(?:[A-Z][a-z]?\d*){2,}", term):
            return re.compile(re.escape(term) + r"(?![a-z0-9])"), False
        return re.compile(re.escape(fold(term))), True

    patterns = [pattern(t, an_id) for t, an_id in zip(terms, is_id)]
    # Ids alone are separate lookups (`recall L177 H145` wants both entries);
    # with a word among them, everything has to match one entry.
    need = any if all(is_id) else all

    def counts(text: str) -> list[int]:
        plain = text.translate(SUBSCRIPTS)
        return [len(p.findall(plain.lower() if folded else plain)) for p, folded in patterns]

    def score(text: str) -> int:
        """How often the terms appear; 0 unless enough of them do."""
        found = counts(text)
        return sum(found) if need(found) else 0

    shown = []

    def show(title: str, found: list[tuple[int, str]]) -> None:
        if not found:
            return
        shown.append(title)
        found.sort(key=lambda pair: -pair[0])
        print(f"\n# {title}: {len(found)}" + (f", best {limit} shown" if len(found) > limit else ""))
        for _, text in found[:limit]:
            print(text)

    named = {t.upper() for t in terms}
    found = []
    for lesson in lessons():
        hits = score(f"{lesson['id']} {lesson['text']}")
        if hits:
            # A lesson asked for by id comes first, in whatever it was asked with.
            found.append((hits + (1000 if lesson["id"] in named else 0), f"\n{lesson['id']} ({lesson['section']}) {lesson['text']}"))
    show("Lessons, in full", found)

    found = [
        (hits, "- " + "; ".join(f"{column}: {cell}" for column, cell in row.items() if cell))
        for row in instruments()
        if (hits := score(" ".join(row.values())))
    ]
    show("Instruments: what the route has been shown to do here (instruments.md)", found)

    slugs = [only] if only else sorted(p.name for p in CAMPAIGNS.glob("*") if p.is_dir())
    for slug in slugs:
        found = []
        for h in hypotheses(slug):
            if hits := score(f"{h['heading']}\n{h['body']}"):
                verdict = re.search(r"^- Verdict:.*", h["body"], re.M)
                if h["id"] in named:  # asked for by id: the whole block
                    found.append((hits + 1000, f"\n## {h['heading']}\n{h['body'][:5000]}"))
                    continue
                found.append((hits, f"- {cut(h['heading'], 200)}\n  {cut(verdict.group(0)[2:], 500) if verdict else 'no verdict line'}"))
        show(f"{slug}: hypotheses (heading and verdict)", found)

        found = [
            (hits + (1000 if c["id"] in named else 0), c["row"] if c["id"] in named else cut(c["row"], 500))
            for c in candidates(slug)
            if (hits := score(c["row"]))
        ]
        show(f"{slug}: candidates", found)

        found = []
        for heading, body in log_entries(slug):
            if hits := score(f"{heading}\n{body}"):
                lines = sorted(
                    (line for line in body.splitlines() if any(counts(line))),
                    key=lambda line: -sum(bool(n) for n in counts(line)),
                )
                found.append((hits, f"- {cut(heading, 200)}\n" + "\n".join(f"    {cut(line, 320)}" for line in lines[:3])))
        show(f"{slug}: log entries (the lines that match; read the entry in log.md for the rest)", found)

        if (CAMPAIGNS / slug / "experiments.jsonl").exists():
            ledger_rows = experiments(slug)
            for t in terms:
                if t.upper() in ledger_rows:
                    shown.append(t)
                    print(f"\n# {slug}: {t.upper()} in the ledger\n{cut(json.dumps(ledger_rows[t.upper()], ensure_ascii=False), 1500)}")
    if not shown:
        print(
            f"Nothing in the notebook has all of: {', '.join(terms)}. Words have to match one entry "
            "together, so recall one topic at a time, or try another spelling, before concluding it is new here."
        )


def index_row(slug: str, status: str, best: str, day: str = "") -> None:
    """Rewrite the campaign's one row in index.md. The row says where the
    campaign stands now; what each loop found is in its log.md."""
    best = " ".join(best.split()).replace("|", "/")
    if len(best) > BEST_BUDGET:
        sys.exit(
            f"Best so far is {len(best)} characters; an index row gets {BEST_BUDGET}. Name the standing "
            "candidate with its headline numbers, or say there is none. The loop history is in log.md."
        )
    path = ROOT / "index.md"
    lines = path.read_text().splitlines()
    mine = [i for i, line in enumerate(lines) if line.startswith(f"| [{slug}](")]
    if not mine:
        sys.exit(f"No row for {slug} in index.md; `notebook.py new` adds one.")
    goal = lines[mine[0]].split("|")[2].strip()
    row = f"| [{slug}](campaigns/{slug}/campaign.md) | {goal} | {status} | {best} | {day or date.today().isoformat()} |"
    lines = [row if i == mine[0] else line for i, line in enumerate(lines) if i == mine[0] or i not in mine]
    path.write_text("\n".join(lines) + "\n")
    print(row)


def check(slugs: list[str]) -> None:
    """Report ids used twice, state pages that have turned into histories,
    runs nobody collected, and claims the ladder does not carry. Nothing is
    renumbered: other files cite the ids."""
    clean, shared = True, False
    for slug in slugs or sorted(p.name for p in CAMPAIGNS.glob("*") if (p / "experiments.jsonl").exists()):
        problems = [f"experiments {', '.join(sorted(reused(slug)))}"] if reused(slug) else []
        for kind, (name, pattern) in MARKDOWN_IDS.items():
            path = CAMPAIGNS / slug / name
            numbers = pattern.findall(path.read_text()) if path.exists() else []
            twice = sorted({f"{kind}{n}" for n in numbers if numbers.count(n) > 1})
            if twice:
                problems.append(f"{name} {', '.join(twice)}")
        if problems:
            clean, shared = False, True
            print(f"{slug}: ids used twice: " + "; ".join(problems))
        size = len((CAMPAIGNS / slug / "campaign.md").read_text())
        if size > CAMPAIGN_BUDGET:
            clean = False
            print(
                f"{slug}: campaign.md is {size} characters, over {CAMPAIGN_BUDGET}. It is the state page: "
                "rewrite Status, Best so far and Map as they stand now; the history is in log.md."
            )
        for eid, advice in stale(slug).items():
            clean = False
            print(f"{slug}: {eid} {advice}")
        for problem in ladder_problems(slug):
            clean = False
            print(f"{slug}: {problem}")
        if bad := malformed(slug):
            clean = False
            print(f"{slug}: {len(bad)} ledger lines with a malformed id, ignored everywhere: {', '.join(cut(b, 12) for b in bad[:6])}{' …' if len(bad) > 6 else ''}")
    numbers = LEARNING_ID.findall((ROOT / "learnings.md").read_text())
    twice = sorted({f"L{n}" for n in numbers if numbers.count(n) > 1})
    if twice:
        clean, shared = False, True
        print(f"learnings.md: ids used twice: {', '.join(twice)}")
    rows = Counter()
    for line in (ROOT / "index.md").read_text().splitlines():
        if m := re.match(r"\| \[([^\]]+)\]\(", line):
            rows[m.group(1)] += 1
            if len(line.split("|")[4].strip()) > BEST_BUDGET:
                clean = False
                print(f"index.md: the {m.group(1)} row's Best so far is over {BEST_BUDGET} characters; rewrite it with `notebook.py index`.")
    for slug, n in rows.items():
        if n > 1:
            clean = False
            print(f"index.md: {n} rows for {slug}; `notebook.py index` leaves one.")
    if clean:
        print("Nothing to fix.")
    elif shared:
        print("An id used twice is cited with its campaign and date, never renumbered; new ids come from `notebook.py next-id`.")


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit(__doc__)
    cmd, args = argv[0], argv[1:]
    if cmd in ("-h", "--help") or "-h" in args or "--help" in args:
        print(__doc__)
        return
    if cmd == "init":
        init()
    elif cmd == "new" and len(args) == 2:
        new(*args)
    elif cmd == "open":
        open_runs()
    elif cmd == "calibration" and len(args) == 1:
        calibration(args[0])
    elif cmd == "spend" and args and len(args) <= 2:
        spend(args[0], ids="--ids" in args)
    elif cmd == "brief" and len(args) == 1:
        brief(args[0])
    elif cmd == "learnings" and not args:
        learnings_index()
    elif cmd == "recall":
        recall(args)
    elif cmd == "index" and len(args) in (3, 4):
        index_row(*args)
    elif cmd == "next-id":
        next_id(args)
    elif cmd == "check":
        check(args)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
