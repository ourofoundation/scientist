"""LLM-written run reports published to Ouro.

The harness gathers an inventory of every Ouro asset the run produced
(structures, phase diagrams, route actions, a candidates dataset). The LLM
writes the whole post as Ouro extended markdown and may only cite ids from
that inventory; anything else is stripped before publishing.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from ..computational.catalog import CATALOG
from ..core.objective import ObjectiveSpec
from ..core.search import _candidate_table, _node_line, annotate_equivalents
from .logging import get_logger

logger = get_logger("report")

REPORT_SYSTEM = """You write the report for one run of an autonomous materials
search, published as a post on Ouro for a scientist who was not watching.

The search is an annealing walk: at each step a model proposed moves with a
predicted score, one was run, and the result was accepted or rejected. You are
given the objective, the full record of this run's steps, earlier runs in
brief, every measured candidate, and an inventory of Ouro assets.

Write the report a careful researcher would want: lead with what this run
found and how far the best candidate is from the objective, then the reasoning
path — why each step was taken, what was predicted, what was measured, where
the prediction was wrong and what that changed. Compare candidates with tables
of the measured numbers and their units. Say plainly what failed, what was not
measured, and how much weight the numbers can bear: these are ML-potential and
DFT predictions, not synthesis. End with what the next run should try.

Embed assets where they support the point being made — the best structure
where it is discussed, a phase diagram next to the system it describes, the
candidates dataset next to the comparison. Use only ids from the inventory.

Ouro markdown:
- Embed an asset as its own block:
  ```assetComponent
  {"id": "<uuid>", "assetType": "file", "viewMode": "preview"}
  ```
  assetType is "file", "dataset", or "post". For a dataset view add
  "displayConfig": {"visualizationId": "<view uuid>"}.
- Link an asset inline with [label](file:<uuid>) or [label](dataset:<uuid>),
  and a route run with [label](action:<uuid>).
- Standard markdown headings, tables, lists and LaTeX-free plain text.

Reply in exactly this form:
TITLE: <specific title naming the main result, under 100 characters>
DESCRIPTION: <one or two sentences>
---
<the report body in markdown, without repeating the title as a heading>"""

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_EMBED = re.compile(r"```assetComponent\s*\n(.*?)\n```", re.DOTALL)
_LINK = re.compile(
    rf"\[([^\]]*)\]\((?:file|dataset|post|route|service|asset|action):({_UUID})\)"
)


def build_inventory(
    nodes: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    dataset: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Every citable Ouro id from a run, with what it is."""
    items: List[Dict[str, Any]] = []
    node_ids = {n["id"] for n in nodes}

    for node in nodes:
        assets = node.get("assets") or {}
        labels = {
            "phase_diagram": ("file", "phase diagram"),
            "report": ("post", "GGen exploration report"),
            "candidate_cifs": ("file", "zip of near-hull CIFs"),
            "action_id": ("action", "GGen exploration run"),
        }
        for key, (kind, label) in labels.items():
            if assets.get(key):
                items.append(
                    {
                        "id": assets[key],
                        "type": kind,
                        "what": f"{label} for {node['system']} (node #{node['id']})",
                    }
                )

    for cand in candidates:
        touched = cand["node_id"] in node_ids or any(
            cand["id"] in (n["move"].get("candidate_ids") or []) for n in nodes
        )
        if not touched:
            continue
        if cand.get("file_id"):
            items.append(
                {
                    "id": cand["file_id"],
                    "type": "file",
                    "what": f"crystal structure (CIF) of candidate {cand['id']} "
                    f"{cand['formula']}",
                }
            )
        for prop, artifact in (cand.get("artifacts") or {}).items():
            if artifact.get("action_id"):
                items.append(
                    {
                        "id": artifact["action_id"],
                        "type": "action",
                        "what": f"{prop} calculation for candidate {cand['id']} "
                        f"{cand['formula']}",
                    }
                )
            for name, asset in (artifact.get("output_assets") or {}).items():
                items.append(
                    {
                        "id": asset["id"],
                        "type": asset.get("asset_type") or "file",
                        "what": f"{prop} output '{name}' for candidate "
                        f"{cand['id']} {cand['formula']}",
                    }
                )

    if dataset:
        items.append(
            {
                "id": dataset["id"],
                "type": "dataset",
                "what": "table of every candidate measured in this campaign",
            }
        )
        for view in dataset.get("views", []):
            items.append(
                {
                    "id": view["id"],
                    "type": "dataset view",
                    "what": f"chart '{view['name']}' — use as visualizationId "
                    f"with dataset {dataset['id']}",
                }
            )
    return items


def sanitize(markdown: str, allowed_ids: set) -> str:
    """Drop embeds and links whose ids are not in the inventory."""
    allowed = {str(i).lower() for i in allowed_ids}

    def embed(match: re.Match) -> str:
        try:
            block = json.loads(match.group(1))
        except json.JSONDecodeError:
            return ""
        ids = [block.get("id")]
        display = block.get("displayConfig") or {}
        ids += [display.get(k) for k in ("visualizationId", "actionId") if display.get(k)]
        if all(i and str(i).lower() in allowed for i in ids):
            return match.group(0)
        logger.warning(f"Removing embed with unknown id: {block.get('id')}")
        return ""

    def link(match: re.Match) -> str:
        if match.group(2).lower() in allowed:
            return match.group(0)
        logger.warning(f"Unlinking unknown id: {match.group(2)}")
        return match.group(1)

    return _LINK.sub(link, _EMBED.sub(embed, markdown))


def parse_reply(reply: str) -> Tuple[Optional[str], Optional[str], str]:
    """Split a TITLE/DESCRIPTION/--- reply into its parts."""
    head, sep, body = reply.partition("\n---")
    if not sep:
        return None, None, reply.strip()
    title = re.search(r"^TITLE:\s*(.+)$", head, re.MULTILINE)
    description = re.search(r"^DESCRIPTION:\s*(.+)$", head, re.MULTILINE | re.DOTALL)
    return (
        title.group(1).strip() if title else None,
        description.group(1).strip() if description else None,
        body.strip(),
    )


def candidate_rows(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flat rows for the candidates dataset; one column per catalog property."""
    rows = []
    for c in candidates:
        row = {
            "candidate": c["id"],
            "formula": c["formula"],
            "chemical_system": c["system"],
            "score": round(c["score"], 4),
            "structure": c.get("file_id"),
        }
        for name in CATALOG:
            row[name] = c["properties"].get(name)
        rows.append(row)
    return rows


def fallback_body(result: Dict[str, Any], candidates: List[Dict[str, Any]]) -> str:
    """Plain record of the run, used when the LLM report cannot be written."""
    lines = ["## Steps", *(_node_line(n, detail=True) for n in result["nodes"])]
    lines += ["", "## Measured candidates", _candidate_table(candidates[:30])]
    return "\n".join(lines)


class Reporter:
    """Creates the run post, then replaces it with the LLM-written report."""

    def __init__(self, config, llm, store) -> None:
        import os

        from ouro import Ouro

        base_url = os.getenv("OURO_BACKEND_URL") or os.getenv("OURO_BASE_URL")
        self.ouro = Ouro(api_key=config.ouro_api_key, base_url=base_url)
        self.team_id = config.ouro_team_id
        self.visibility = config.ouro_asset_visibility
        self.llm = llm
        self.store = store

    def start(self, campaign: Dict[str, Any]) -> str:
        """Create the placeholder post that run assets are parented to."""
        objective = ObjectiveSpec.model_validate(campaign["objective"])
        post = self.ouro.posts.create(
            name=f"Scientist run in progress: {objective.title}"[:120],
            content_markdown=(
                "Run started. This post will be replaced by the report.\n\n"
                f"## Objective\n{objective.describe()}"
            ),
            description=objective.summary[:300],
            visibility=self.visibility,
            team_id=self.team_id,
        )
        logger.info(f"Created run post: {post.id}")
        return str(post.id)

    def publish(self, result: Dict[str, Any], post_id: str) -> None:
        campaign = result["campaign"]
        objective = ObjectiveSpec.model_validate(campaign["objective"])
        candidates = [
            c for c in self.store.candidates(campaign["id"]) if c["evaluated"]
        ]
        dataset = self._create_dataset(objective, candidates, post_id)
        inventory = build_inventory(result["nodes"], candidates, dataset)

        title, description, body = None, None, None
        try:
            reply = self.llm.text(
                REPORT_SYSTEM,
                self._prompt(result, objective, candidates, inventory),
                purpose="report",
            )
            title, description, body = parse_reply(reply)
            body = sanitize(body, {item["id"] for item in inventory})
        except Exception as e:
            logger.exception(f"Report writing failed, publishing the plain record: {e}")
        if not body:
            body = fallback_body(result, candidates)

        llm = self.store.usage(result["run_id"])["llm"]
        routes = self.store.usage(result["run_id"])["routes"]
        body += (
            f"\n\n---\n*{len(result['nodes'])} steps this run · model "
            f"{self.llm.model} · {llm['calls']} LLM calls (${llm['cost_usd']:.2f}, "
            f"before this report) · {routes['calls']} Ouro route runs "
            f"(${routes['cost_usd']:.2f}, {routes['seconds'] / 60:.0f} min)*"
        )

        content = self.ouro.posts.Content()
        content.from_markdown(body)
        self.ouro.posts.update(
            id=post_id,
            content=content,
            name=(title or f"Scientist run: {objective.title}")[:120],
            description=description or objective.summary[:300],
            visibility=self.visibility,
            team_id=self.team_id,
        )
        logger.info(f"Published report to post {post_id}")

    def _create_dataset(
        self,
        objective: ObjectiveSpec,
        candidates: List[Dict[str, Any]],
        post_id: str,
    ) -> Optional[Dict[str, Any]]:
        if not candidates:
            return None
        try:
            dataset = self.ouro.datasets.create(
                name=f"Candidates: {objective.title}"[:120],
                visibility=self.visibility,
                data=candidate_rows(candidates),
                description="Every candidate measured so far in this campaign.",
                team_id=self.team_id,
                parent_id=post_id,
            )
        except Exception as e:
            logger.warning(f"Could not create candidates dataset: {e}")
            return None

        info: Dict[str, Any] = {"id": str(dataset.id), "views": []}
        props = [p for p in objective.properties() if not CATALOG[p].boolean]
        if len(props) >= 2:
            try:
                view = self.ouro.datasets.create_view(
                    info["id"],
                    name=f"{props[0]} vs {props[1]}",
                    prompt=(
                        f"Scatter plot of {props[0]} against {props[1]} for each "
                        "candidate, labelled by formula."
                    ),
                )
                info["views"].append({"id": str(view.id), "name": view.name})
            except Exception as e:
                logger.warning(f"Could not create dataset view: {e}")
        return info

    def _prompt(
        self,
        result: Dict[str, Any],
        objective: ObjectiveSpec,
        candidates: List[Dict[str, Any]],
        inventory: List[Dict[str, Any]],
    ) -> str:
        campaign = result["campaign"]
        run_nodes = result["nodes"]
        run_node_ids = {n["id"] for n in run_nodes}
        earlier = [
            n for n in self.store.nodes(campaign["id"]) if n["id"] not in run_node_ids
        ]

        steps = []
        for node in run_nodes:
            move = node["move"]
            alternatives = "\n".join(
                f"    - {a['kind']} {a['target']} (predicted "
                f"{a['predicted_score']:.2f}): {a['rationale']}"
                for a in move.get("alternatives") or []
            )
            steps.append(
                f"{_node_line(node, detail=True)}\n"
                f"  temperature: {node['temperature']}\n"
                f"  prediction: {move.get('prediction')}\n"
                f"  surprise: {node.get('surprise')}\n"
                f"  exploration summary: {json.dumps(node.get('summary'))}\n"
                f"  moves proposed but not taken:\n{alternatives or '    (none)'}"
            )

        learnings = self.store.learnings(campaign["id"], limit=100)
        new = [l for l in learnings if l["node_id"] in run_node_ids]
        old = [l for l in learnings if l["node_id"] not in run_node_ids]

        return "\n\n".join(
            [
                f"## Goal as given\n{campaign['goal']}",
                f"## Objective\n{objective.describe()}",
                f"## Campaign state\n{campaign['steps_done']} steps over "
                f"{len(self.store.runs(campaign['id']))} runs; best score "
                f"{campaign['best_score']:.3f}; temperature now "
                f"{campaign['temperature']:.3f}.",
                "## Earlier runs (brief)\n"
                + ("\n".join(_node_line(n, detail=False) for n in earlier) or "(none)"),
                "## Learnings carried in from earlier runs\n"
                + ("\n".join(f"- {l['text']}" for l in old) or "(none)"),
                "## This run, step by step\n" + "\n\n".join(steps),
                "## Learnings recorded this run\n"
                + ("\n".join(f"- {l['text']}" for l in new) or "(none)"),
                "## All measured candidates (best first)\n"
                + _candidate_table(
                    [
                        c
                        for c in annotate_equivalents(
                            self.store.candidates(campaign["id"])
                        )
                        if c["evaluated"]
                    ][:60]
                ),
                "## Asset inventory (the only ids you may cite)\n"
                + (
                    "\n".join(
                        f"- {i['id']} [{i['type']}] {i['what']}" for i in inventory
                    )
                    or "(none)"
                ),
            ]
        )
