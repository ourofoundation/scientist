from __future__ import annotations

from typing import Dict, Optional, List, Tuple
import os
import datetime as _dt
import json

from ouro import Ouro
import pandas as pd


class Publisher:
    def __init__(self) -> None:
        self.ouro = Ouro(api_key=os.getenv("OURO_API_KEY"))
        self.team_id = os.getenv("OURO_TEAM_ID")
        self.publish_route = os.getenv("OURO_PUBLISH_ROUTE")

    def publish_run_summary(
        self,
        discovery: Dict,
        targets: Optional[Dict] = None,
        run_title: Optional[str] = None,
    ) -> Optional[Dict]:
        if not discovery:
            return None

        print("publishing run summary")
        print(discovery)

        best = discovery.get("best_material")
        all_results: List[Dict] = discovery.get("all_results", [])
        if not best:
            title = run_title or "Scientist run: no best material found"
            content = self._build_content_none(all_results, targets, title)
            return self._create_post(title, content)

        structure_asset = best.get("structure_file")

        phase_asset = best["artifacts"]["mmoderwell/post-materials-thermo-ehull"][
            "file"
        ]

        title = run_title or self._default_title(best)
        content = self._build_content_best(
            best=best,
            all_results=all_results,
            targets=targets,
            structure_asset=structure_asset,
            phase_asset=phase_asset,
            title=title,
        )

        return self._create_post(title, content)

    def _default_title(self, best: Dict) -> str:
        composition = best["composition"]
        score = best["score"]
        return f"Scientist discovery: {composition} (score {score:.3f})"

    def _build_content_none(
        self, all_results: List[Dict], targets: Optional[Dict], title: str
    ) -> "Editor":
        editor = self.ouro.posts.Editor()
        editor.new_header(level=1, text=title)
        editor.new_paragraph(text="No best material was selected in this run.")
        if targets:
            editor.new_header(level=2, text="Targets")
            # Render targets as a small table (key/value)
            df = pd.DataFrame(
                {"Target": list(targets.keys()), "Value": list(targets.values())}
            )
            editor.new_table(df)
        if all_results:
            editor.new_header(level=2, text="Iterations (scores)")
            df_iters = pd.DataFrame(
                [
                    {
                        "iteration": r.get("iteration"),
                        "composition": r.get("composition"),
                        "score": r.get("score"),
                    }
                    for r in all_results
                ]
            )
            editor.new_table(df_iters)
        return editor

    def _build_content_best(
        self,
        best: Dict,
        all_results: List[Dict],
        targets: Optional[Dict],
        structure_asset: Optional[Dict],
        phase_asset: Optional[Dict],
        title: str,
    ) -> "Editor":
        editor = self.ouro.posts.Editor()
        editor.new_header(level=1, text=title)

        # Insights about the best candidate
        insights = best.get("insights")
        if insights:
            editor.new_header(level=2, text="Summary")
            editor.new_paragraph(text=insights)

        # Summary table
        composition = best.get("composition")
        sg_used = best.get("space_group_resolved") or best.get("space_group_used")
        score = best.get("score")
        summary_table = pd.DataFrame(
            [
                ("composition", composition),
                ("space group", sg_used),
                ("score", f"{score:.3f}"),
                ("number of trials", len(all_results)),
            ]
        )
        editor.new_table(summary_table)

        # Embed structure as inline asset
        if structure_asset and structure_asset.get("id"):
            editor.new_header(level=2, text="Structure")
            editor.new_inline_asset(
                id=structure_asset["id"], asset_type="file", view_mode="chart"
            )

        # Hypothesis
        hypothesis = best.get("hypothesis")
        if hypothesis:
            editor.new_paragraph(text=hypothesis)

        # Predicted properties table
        properties: Dict = best.get("properties", {})
        if properties:
            editor.new_header(level=2, text="Predicted properties")
            df_props = pd.DataFrame(
                {
                    "Property": list(properties.keys()),
                    "Value": list(properties.values()),
                }
            )
            editor.new_table(df_props)

        # Targets table
        if targets:
            editor.new_header(level=2, text="Targets")
            df_targets = pd.DataFrame(
                {"Target": list(targets.keys()), "Value": list(targets.values())}
            )
            editor.new_table(df_targets)

        # Phase diagram embed
        if phase_asset and phase_asset.get("id"):
            editor.new_header(level=2, text="Phase diagram")
            editor.new_inline_asset(
                id=phase_asset["id"], asset_type="file", view_mode="chart"
            )

        # Iterations table
        if all_results:
            editor.new_header(level=2, text="Iterations")
            df_iters = pd.DataFrame(
                [
                    {
                        "iteration": r.get("iteration"),
                        "composition": r.get("composition"),
                        "sg": r.get("space_group_resolved")
                        or r.get("space_group_used"),
                        "score": r.get("score"),
                    }
                    for r in all_results
                ]
            )
            editor.new_table(df_iters)

        return editor

    def _get_artifact(self, best: Dict, route_name: str) -> Optional[Dict]:
        artifacts = best.get("artifacts") or {}
        response = artifacts.get(route_name)
        return response["file"]["id"]

    def _create_post(self, title: str, content: "Editor") -> Optional[Dict]:
        visibility = os.getenv("OURO_POST_VISIBILITY", "public")
        description = os.getenv(
            "OURO_POST_DESCRIPTION",
            "Automated run summary with structure, phase diagram, and insights.",
        )

        post = self.ouro.posts.create(
            content=content,
            name=title,
            description=description,
            visibility=visibility,
            team_id=self.team_id,
        )
        return post.model_dump(mode="json") if hasattr(post, "model_dump") else post
