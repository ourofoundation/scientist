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
        """Initialize publisher with configuration.

        Args:
            config: ScientistConfig instance with Ouro settings
        """
        self.ouro = Ouro(api_key=config.ouro_api_key)
        self.team_id = config.ouro_team_id
        self.visibility = config.ouro_asset_visibility

    def create_initial_post(
        self,
        run_title: Optional[str] = None,
        targets: Optional[Dict] = None,
        description: Optional[str] = None,
    ) -> Optional[Dict]:
        """Create an initial placeholder post at the start of a run.

        Returns the created post (dict) with its id for downstream asset parenting.
        """
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
        """Publish a discovery run summary to Ouro.

        Args:
            discovery: Discovery results dictionary
            targets: Target properties used in discovery
            run_title: Optional custom title for the post
            post_id: If provided, update this existing post instead of creating new
            token_usage: Optional token usage statistics from DSPy LM

        Returns:
            Published post data or None if no discovery
        """
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
        phase_asset = self._get_artifact(best, "mmoderwell/calculate-energy-above-hull")

        title = run_title or self._default_title(best)
        content = self._build_content_best(
            best=best,
            all_results=all_results,
            targets=targets,
            structure_asset=structure_asset,
            phase_asset=phase_asset,
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
        """Generate default title for discovery post."""
        composition = best["composition"]
        score = best["score"]

        sg_info = ""
        if best.get("space_group_resolved"):
            sg_info = f" SG #{best['space_group_resolved']}"
        elif best.get("space_group_used"):
            sg_info = f" SG #{best['space_group_used']}"

        return f"AI Scientist: {composition}{sg_info} (score {score:.3f})"

    def _build_content_none(
        self,
        all_results: List[Dict],
        targets: Optional[Dict],
        title: str,
        token_usage: Optional[Dict] = None,
    ) -> "Editor":
        """Build content when no best material was found."""
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
            editor.new_header(level=2, text="Iterations (scores)")
            df_iters = pd.DataFrame(
                [
                    {
                        "iteration": r.get("iteration"),
                        "composition": r.get("composition"),
                        "method": r.get("generation_method", "from_scratch"),
                        "score": r.get("score"),
                    }
                    for r in all_results
                ]
            )
            editor.new_table(df_iters)

        # Token usage section
        if token_usage:
            self._add_token_usage_section(editor, token_usage)

        return editor

    def _build_content_best(
        self,
        best: Dict,
        all_results: List[Dict],
        targets: Optional[Dict],
        structure_asset: Optional[Dict],
        phase_asset: Optional[Dict],
        title: str,
        discovery: Dict,
        token_usage: Optional[Dict] = None,
    ) -> "Editor":
        """Build content for successful discovery."""
        editor = self.ouro.posts.Editor()
        editor.new_header(level=1, text=title)

        # Hypothesis
        hypothesis = best.get("hypothesis")
        if hypothesis:
            editor.new_header(level=2, text="Hypothesis")
            hypothesis_content = self.ouro.posts.Editor()
            hypothesis_content.from_markdown(hypothesis)
            editor.append(hypothesis_content)

        # Summary table
        composition = best.get("composition")
        sg_used = best.get("space_group_resolved") or best.get("space_group_used")
        score = best.get("score")
        generation_method = best.get("generation_method", "from_scratch")
        parent_id = best.get("parent_material_id", "none")

        summary_data = [
            ("composition", composition),
            ("space group", sg_used),
            ("score", f"{score:.3f}"),
            ("generation method", generation_method),
            ("number of trials", len(all_results)),
        ]

        if generation_method == "mutation" and parent_id != "none":
            summary_data.insert(-1, ("parent material", parent_id))

        summary_table = pd.DataFrame(summary_data, columns=["Property", "Value"])
        editor.new_table(summary_table)

        # Structure embed
        if structure_asset and structure_asset.get("id"):
            editor.new_header(level=2, text="Structure")
            editor.new_inline_asset(
                id=structure_asset["id"], asset_type="file", view_mode="preview"
            )

        # Properties table
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

        # Insights
        insights = best.get("insights")
        if insights:
            editor.new_header(level=2, text="Summary")
            insights_content = self.ouro.posts.Editor()
            insights_content.from_markdown(insights)
            editor.append(insights_content)

        # Phase diagram
        if phase_asset and phase_asset.get("id"):
            editor.new_header(level=2, text="Phase diagram")
            editor.new_inline_asset(
                id=phase_asset["id"], asset_type="file", view_mode="preview"
            )

        # Trajectory visualization
        trajectory_viz = discovery.get("trajectory_visualization")
        if trajectory_viz and trajectory_viz.get("visualization"):
            editor.new_header(level=2, text="Mutation Trajectory Visualization")
            frame_count = trajectory_viz.get("frame_count", 0)
            if frame_count > 0:
                editor.new_paragraph(
                    text=f"Interactive visualization showing the evolution of "
                    f"{frame_count} structural frames during the mutation process."
                )

            viz_result = trajectory_viz["visualization"]
            if viz_result.get("file") and viz_result["file"].get("id"):
                editor.new_inline_asset(
                    id=viz_result["file"]["id"], asset_type="file", view_mode="preview"
                )

        # Mutation analysis
        mutation_summary = discovery.get("mutation_summary", {})
        if mutation_summary:
            editor.new_header(level=2, text="Mutation Analysis")
            mut_data = [
                {
                    "mutation_type": mut_type,
                    "success_rate": f"{stats['success_rate']:.2%}",
                    "attempts": stats["total_attempts"],
                }
                for mut_type, stats in mutation_summary.items()
            ]
            if mut_data:
                df_mutations = pd.DataFrame(mut_data)
                editor.new_table(df_mutations)

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
                        "method": r.get("generation_method", "from_scratch"),
                        "score": r.get("score"),
                    }
                    for r in all_results
                ]
            )
            editor.new_table(df_iters)

        # Token usage section
        if token_usage:
            self._add_token_usage_section(editor, token_usage)

        return editor

    def _add_token_usage_section(self, editor: "Editor", token_usage: Dict) -> None:
        """Add token usage and cost section to the report.

        Args:
            editor: The Ouro Editor instance
            token_usage: Token usage statistics dictionary
        """
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

        df_usage = pd.DataFrame(usage_data, columns=["Metric", "Value"])
        editor.new_table(df_usage)

    def _get_artifact(self, best: Dict, route_name: str) -> Optional[Dict]:
        """Safely extract artifact from best material results.

        Args:
            best: Best material dictionary
            route_name: Name of the route to get artifact for

        Returns:
            Artifact file dict if available, None otherwise
        """
        try:
            artifacts = best.get("artifacts") or {}
            route_result = artifacts.get(route_name) or {}
            return route_result.get("file")
        except (KeyError, TypeError):
            logger.debug(f"Artifact not found for route: {route_name}")
            return None

    def _create_post(
        self, title: str, content: "Editor", description: Optional[str] = None
    ) -> Optional[Dict]:
        """Create and publish a post to Ouro."""
        if description is None:
            description = (
                "Automated run summary with structure, phase diagram, and insights."
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
        """Update an existing post on Ouro."""
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
        """Generate a rich post description using available metadata."""
        parts = []

        # Basic info
        composition = best.get("composition", "Unknown")
        score = best.get("score", 0)
        parts.append(
            f"AI-discovered magnetic material: {composition} "
            f"(performance score: {score:.3f})"
        )

        # Space group
        sg_resolved = best.get("space_group_resolved")
        sg_used = best.get("space_group_used")
        if sg_resolved:
            parts.append(f"Space group: {sg_resolved} (resolved)")
        elif sg_used:
            parts.append(f"Space group: {sg_used}")

        # Generation method
        generation_method = best.get("generation_method", "from_scratch")
        if generation_method == "mutation":
            parent_id = best.get("parent_material_id", "")
            if parent_id and parent_id != "none":
                parts.append(f"Mutated from {parent_id}")
                mutation_history = best.get("mutation_history", [])
                if mutation_history:
                    last_mutation = mutation_history[-1]
                    mutation_type = last_mutation.get("mutation_type", "unknown")
                    parts.append(f"Last mutation: {mutation_type}")
        else:
            parts.append("Generated from scratch")

        # Key properties
        properties = best.get("properties", {})
        if properties:
            prop_summary = []
            if "curie_temperature" in properties and properties["curie_temperature"]:
                prop_summary.append(f"Tc: {properties['curie_temperature']:.0f}K")
            if "magnetic_density" in properties and properties["magnetic_density"]:
                prop_summary.append(f"Ms: {properties['magnetic_density']:.2f}T")
            if "cost" in properties and properties["cost"]:
                prop_summary.append(f"${properties['cost']:.0f}/kg")
            if prop_summary:
                parts.append("Properties: " + ", ".join(prop_summary))

        # Discovery context
        iterations_run = discovery.get("iterations_run", 0)
        parts.append(f"Discovered in {iterations_run} iterations")

        return " | ".join(parts)
