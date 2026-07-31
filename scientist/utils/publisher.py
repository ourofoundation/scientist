"""Publishing utilities for discovery results."""

from __future__ import annotations

from typing import Dict, Optional, List, Any, TYPE_CHECKING

from ouro import Ouro
import pandas as pd

from .logging import get_logger

if TYPE_CHECKING:
    from ouro.posts import Editor

logger = get_logger("publisher")


class Publisher:
    """Publishes discovery results to Ouro platform."""

    def __init__(self, config) -> None:
        import os

        base_url = os.getenv("OURO_BACKEND_URL") or os.getenv("OURO_BASE_URL")
        self.ouro = Ouro(api_key=config.ouro_api_key, base_url=base_url)
        self.team_id = config.ouro_team_id
        self.visibility = config.ouro_asset_visibility

    def create_initial_post(
        self,
        run_title: Optional[str] = None,
        targets: Optional[Dict] = None,
        description: Optional[str] = None,
    ) -> Optional[Dict]:
        editor = self.ouro.posts.Editor()
        editor.new_paragraph(
            text="Run started. This post will be updated with results."
        )
        if targets:
            editor.new_header(level=2, text="Targets")
            df = pd.DataFrame(
                {"Target": list(targets.keys()), "Value": list(targets.values())}
            )
            editor.new_table(df)

        post = self._create_post(
            title=run_title, content=editor, description=description
        )
        logger.info(f"Created initial post: {post.get('id') if post else 'None'}")
        return post

    def publish_run_summary(
        self,
        discovery: Dict,
        targets: Optional[Dict] = None,
        run_title: Optional[str] = None,
        post_id: Optional[str] = None,
        token_usage: Optional[Dict] = None,
    ) -> Optional[Dict]:
        if not discovery:
            return None

        logger.info("Publishing run summary")

        best = discovery.get("best_material")
        all_results: List[Dict] = discovery.get("all_results", [])

        if not best:
            title = run_title or "Scientist run: no best material found"
            content = self._build_content_none(all_results, targets, title, token_usage)
            if post_id:
                return self._update_post(post_id=post_id, title=title, content=content)
            return self._create_post(title, content)

        structure_asset = best.get("structure_file")
        title = run_title or self._default_title(best)
        content = self._build_content_best(
            best=best,
            all_results=all_results,
            targets=targets,
            structure_asset=structure_asset,
            title=title,
            discovery=discovery,
            token_usage=token_usage,
        )

        description = self._generate_rich_post_description(best, discovery)

        if post_id:
            return self._update_post(
                post_id=post_id, title=title, content=content, description=description
            )
        return self._create_post(title, content, description)

    def _default_title(self, best: Dict) -> str:
        composition = best["composition"]
        score = best["score"]
        sg = best.get("space_group_resolved") or best.get("space_group_used")
        sg_info = f" SG #{sg}" if sg else ""
        system = best.get("chemical_system")
        system_info = f" [{system}]" if system else ""
        return f"AI Scientist: {composition}{sg_info}{system_info} (score {score:.3f})"

    def _build_content_none(
        self,
        all_results: List[Dict],
        targets: Optional[Dict],
        title: str,
        token_usage: Optional[Dict] = None,
    ) -> "Editor":
        editor = self.ouro.posts.Editor()
        editor.new_header(level=1, text=title)
        editor.new_paragraph(text="No best material was selected in this run.")

        if targets:
            editor.new_header(level=2, text="Targets")
            df = pd.DataFrame(
                {"Target": list(targets.keys()), "Value": list(targets.values())}
            )
            editor.new_table(df)

        if all_results:
            editor.new_header(level=2, text="Candidates (scores)")
            df_iters = pd.DataFrame(
                [
                    {
                        "iteration": r.get("iteration"),
                        "system": r.get("chemical_system"),
                        "composition": r.get("composition"),
                        "score": r.get("score"),
                    }
                    for r in all_results
                ]
            )
            editor.new_table(df_iters)

        if token_usage:
            self._add_token_usage_section(editor, token_usage)

        return editor

    def _build_content_best(
        self,
        best: Dict,
        all_results: List[Dict],
        targets: Optional[Dict],
        structure_asset: Optional[Dict],
        title: str,
        discovery: Dict,
        token_usage: Optional[Dict] = None,
    ) -> "Editor":
        editor = self.ouro.posts.Editor()
        editor.new_header(level=1, text=title)

        hypothesis = best.get("hypothesis")
        if hypothesis:
            editor.new_header(level=2, text="Hypothesis")
            hypothesis_content = self.ouro.posts.Editor()
            hypothesis_content.from_markdown(hypothesis)
            editor.append(hypothesis_content)

        composition = best.get("composition")
        sg_used = best.get("space_group_resolved") or best.get("space_group_used")
        score = best.get("score")
        chemical_system = best.get("chemical_system", "")

        summary_data = [
            ("composition", composition),
            ("chemical system", chemical_system),
            ("space group", sg_used),
            ("score", f"{score:.3f}"),
            ("systems explored", len(discovery.get("explored_systems", []))),
            ("candidates evaluated", len(all_results)),
        ]
        editor.new_table(pd.DataFrame(summary_data, columns=["Property", "Value"]))

        if structure_asset and structure_asset.get("id"):
            editor.new_header(level=2, text="Structure")
            editor.new_inline_asset(
                id=structure_asset["id"], asset_type="file", view_mode="preview"
            )

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

        insights = best.get("insights")
        if insights:
            editor.new_header(level=2, text="Summary")
            insights_content = self.ouro.posts.Editor()
            insights_content.from_markdown(insights)
            editor.append(insights_content)

        # Exploration history
        explorations = discovery.get("exploration_history", [])
        if explorations:
            editor.new_header(level=2, text="Explored Systems")
            df_exp = pd.DataFrame(
                [
                    {
                        "system": e.get("chemical_system"),
                        "structures": e.get("num_successful"),
                        "near_hull": e.get("num_near_hull"),
                        "evaluated": e.get("num_evaluated"),
                        "time_s": round(e.get("time_seconds", 0), 1),
                    }
                    for e in explorations
                ]
            )
            editor.new_table(df_exp)

        if all_results:
            editor.new_header(level=2, text="Evaluated Candidates")
            df_iters = pd.DataFrame(
                [
                    {
                        "iteration": r.get("iteration"),
                        "system": r.get("chemical_system"),
                        "composition": r.get("composition"),
                        "sg": r.get("space_group_resolved")
                        or r.get("space_group_used"),
                        "score": r.get("score"),
                    }
                    for r in all_results
                ]
            )
            editor.new_table(df_iters)

        if token_usage:
            self._add_token_usage_section(editor, token_usage)

        return editor

    def _add_token_usage_section(self, editor: "Editor", token_usage: Dict) -> None:
        editor.new_header(level=2, text="LLM Usage & Cost")
        usage_data = [
            ("Total LLM calls", f"{token_usage.get('total_calls', 0):,}"),
            ("Input tokens", f"{token_usage.get('input_tokens', 0):,}"),
            ("Output tokens", f"{token_usage.get('output_tokens', 0):,}"),
            ("Total tokens", f"{token_usage.get('total_tokens', 0):,}"),
            (
                "Estimated cost (USD)",
                f"${token_usage.get('estimated_cost_usd', 0):.4f}",
            ),
        ]
        editor.new_table(pd.DataFrame(usage_data, columns=["Metric", "Value"]))

    def _create_post(
        self, title: str, content: "Editor", description: Optional[str] = None
    ) -> Optional[Dict]:
        if description is None:
            description = (
                "Automated run summary with structure, exploration, and insights."
            )
        post = self.ouro.posts.create(
            content=content,
            name=title,
            description=description,
            visibility=self.visibility,
            team_id=self.team_id,
        )
        return post.model_dump(mode="json") if hasattr(post, "model_dump") else post

    def _update_post(
        self,
        post_id: str,
        title: str,
        content: "Editor",
        description: Optional[str] = None,
    ) -> Optional[Dict]:
        post = self.ouro.posts.update(
            id=post_id,
            content=content,
            name=title,
            description=description,
            visibility=self.visibility,
            team_id=self.team_id,
        )
        return post.model_dump(mode="json")

    def _generate_rich_post_description(self, best: Dict, discovery: Dict) -> str:
        parts = []
        composition = best.get("composition", "Unknown")
        score = best.get("score", 0)
        parts.append(
            f"AI-discovered magnetic material: {composition} "
            f"(performance score: {score:.3f})"
        )

        if best.get("chemical_system"):
            parts.append(f"System: {best['chemical_system']}")

        sg = best.get("space_group_resolved") or best.get("space_group_used")
        if sg:
            parts.append(f"Space group: {sg}")

        properties = best.get("properties", {})
        if properties:
            prop_summary = []
            if properties.get("curie_temperature"):
                prop_summary.append(f"Tc: {properties['curie_temperature']:.0f}K")
            if properties.get("magnetic_density"):
                prop_summary.append(f"Ms: {properties['magnetic_density']:.2f}T")
            if properties.get("cost"):
                prop_summary.append(f"${properties['cost']:.0f}/kg")
            if prop_summary:
                parts.append("Properties: " + ", ".join(prop_summary))

        systems = discovery.get("explored_systems", [])
        parts.append(
            f"Explored {len(systems)} systems "
            f"({', '.join(systems)}) in {discovery.get('iterations_run', 0)} iterations"
        )
        return " | ".join(parts)
