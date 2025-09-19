"""Publishing utilities for discovery results."""

from __future__ import annotations

from typing import Dict, Optional, List, Tuple
import os
import datetime as _dt
import json

from ouro import Ouro
import pandas as pd


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
        title = None
        editor = self.ouro.posts.Editor()
        # editor.new_header(level=1, text=title)
        editor.new_paragraph(
            text="Run started. This post will be updated with results."
        )
        if targets:
            editor.new_header(level=2, text="Targets")
            df = pd.DataFrame(
                {"Target": list(targets.keys()), "Value": list(targets.values())}
            )
            editor.new_table(df)

        post = self._create_post(title=title, content=editor, description=description)
        return post

    def publish_run_summary(
        self,
        discovery: Dict,
        targets: Optional[Dict] = None,
        run_title: Optional[str] = None,
        post_id: Optional[str] = None,
    ) -> Optional[Dict]:
        """Publish a discovery run summary to Ouro.

        Args:
            discovery: Discovery results dictionary
            targets: Target properties used in discovery
            run_title: Optional custom title for the post
            post_id: If provided, update this existing post instead of creating new

        Returns:
            Published post data or None if no discovery
        """
        if not discovery:
            return None

        print("publishing run summary")

        best = discovery.get("best_material")
        all_results: List[Dict] = discovery.get("all_results", [])
        if not best:
            title = run_title or "Scientist run: no best material found"
            content = self._build_content_none(all_results, targets, title)
            # Use default description when no best material found
            if post_id:
                return self._update_post(post_id=post_id, title=title, content=content)
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
            discovery=discovery,
        )

        # Create rich description using available metadata
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
        generation_method = best.get("generation_method", "from_scratch")

        # Add space group info to title
        sg_info = ""
        if best.get("space_group_resolved"):
            sg_info = f" SG #{best['space_group_resolved']}"
        elif best.get("space_group_used"):
            sg_info = f" SG #{best['space_group_used']}"

        return f"AI Scientist: {composition}{sg_info} (score {score:.3f})"

    def _build_content_none(
        self, all_results: List[Dict], targets: Optional[Dict], title: str
    ) -> "Editor":
        """Build content when no best material was found."""
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
                        "method": r.get("generation_method", "from_scratch"),
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
        discovery: Dict,
    ) -> "Editor":
        """Build content for successful discovery."""
        editor = self.ouro.posts.Editor()
        editor.new_header(level=1, text=title)

        # Hypothesis
        hypothesis = best.get("hypothesis")
        if hypothesis:
            editor.new_header(level=2, text="Hypothesis")
            editor.new_paragraph(text=hypothesis)

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

        summary_table = pd.DataFrame(
            summary_data,
            columns=["Property", "Value"],
        )
        editor.new_table(summary_table)

        # Embed structure as inline asset
        if structure_asset and structure_asset.get("id"):
            editor.new_header(level=2, text="Structure")
            editor.new_inline_asset(
                id=structure_asset["id"], asset_type="file", view_mode="chart"
            )

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

        # Insights about the best candidate
        insights = best.get("insights")
        if insights:
            editor.new_header(level=2, text="Summary")
            editor.new_paragraph(text=insights)

        # Phase diagram embed
        if phase_asset and phase_asset.get("id"):
            editor.new_header(level=2, text="Phase diagram")
            editor.new_inline_asset(
                id=phase_asset["id"], asset_type="file", view_mode="chart"
            )

        # Trajectory visualization
        trajectory_viz = discovery.get("trajectory_visualization")
        if trajectory_viz and trajectory_viz.get("visualization"):
            editor.new_header(level=2, text="Mutation Trajectory Visualization")
            frame_count = trajectory_viz.get("frame_count", 0)
            if frame_count > 0:
                editor.new_paragraph(
                    text=f"Interactive visualization showing the evolution of {frame_count} structural frames during the mutation process."
                )

            # Embed the visualization result from the matterviz route
            viz_result = trajectory_viz["visualization"]
            if viz_result.get("file") and viz_result["file"].get("id"):
                editor.new_inline_asset(
                    id=viz_result["file"]["id"], asset_type="file", view_mode="chart"
                )

        # System Evolution Steps
        # editor.new_header(level=2, text="System Evolution")
        # evolution_steps = self._get_evolution_steps(all_results, discovery)
        # for i, step in enumerate(evolution_steps, 1):
        #     editor.new_paragraph(text=f"**{i}. {step['title']}**")
        #     editor.new_paragraph(text=step["description"])
        #     if step.get("reasoning"):
        #         editor.new_paragraph(text=f"*Reasoning:* {step['reasoning']}")
        #     editor.new_paragraph(text="")  # Add spacing

        # Mutations summary if available
        mutation_summary = discovery.get("mutation_summary", {})
        if mutation_summary:
            editor.new_header(level=2, text="Mutation Analysis")
            mut_data = []
            for mut_type, stats in mutation_summary.items():
                mut_data.append(
                    {
                        "mutation_type": mut_type,
                        "success_rate": f"{stats['success_rate']:.2%}",
                        "attempts": stats["total_attempts"],
                    }
                )
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

        return editor

    def _get_artifact(self, best: Dict, route_name: str) -> Optional[Dict]:
        """Extract artifact from best material results."""
        artifacts = best.get("artifacts") or {}
        response = artifacts.get(route_name)
        return response["file"]["id"]

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
        """Update an existing post on Ouro"""

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
        description_parts = []

        # Basic material info
        composition = best.get("composition", "Unknown")
        score = best.get("score", 0)
        description_parts.append(
            f"AI-discovered magnetic material: {composition} (performance score: {score:.3f})"
        )

        # Space group and structure info
        sg_resolved = best.get("space_group_resolved")
        sg_used = best.get("space_group_used")
        sg_requested = best.get("space_group_requested")

        if sg_resolved:
            description_parts.append(
                f"Space group: {sg_resolved} (resolved from structure)"
            )
        elif sg_used:
            description_parts.append(f"Space group: {sg_used} (used in generation)")
        elif sg_requested:
            description_parts.append(f"Requested space group: {sg_requested}")

        # Generation method and lineage
        generation_method = best.get("generation_method", "from_scratch")
        if generation_method == "mutation":
            parent_id = best.get("parent_material_id", "")
            if parent_id and parent_id != "none":
                description_parts.append(
                    f"Generated via AI-guided mutation from parent material {parent_id}"
                )
                # Add mutation history info
                mutation_history = best.get("mutation_history", [])
                if mutation_history:
                    last_mutation = mutation_history[-1]
                    mutation_type = last_mutation.get("mutation_type", "unknown")
                    description_parts.append(f"Last mutation: {mutation_type}")
        elif generation_method == "from_scratch":
            description_parts.append(
                "AI-generated from scratch using crystal structure prediction"
            )

        # Key properties
        properties = best.get("properties", {})
        if properties:
            prop_summary = []
            if "curie_temperature" in properties:
                tc = properties["curie_temperature"]
                prop_summary.append(f"Tc: {tc:.0f}K")
            if "magnetic_density" in properties:
                md = properties["magnetic_density"]
                prop_summary.append(f"Ms: {md:.2f}T")
            if "cost" in properties:
                cost = properties["cost"]
                prop_summary.append(f"Cost: ${cost:.0f}/kg")
            if "e_hull" in properties:
                eh = properties["e_hull"]
                prop_summary.append(f"E_hull: {eh:.3f}eV/atom")
            if "dynamic_stability" in properties:
                stability = "stable" if properties["dynamic_stability"] else "unstable"
                prop_summary.append(f"Dynamically {stability}")

            if prop_summary:
                description_parts.append("Key properties: " + ", ".join(prop_summary))

        # Discovery context
        iterations_run = discovery.get("iterations_run", 0)
        description_parts.append(f"Discovered in {iterations_run} AI iterations")

        # Mutation analysis if available
        mutation_summary = discovery.get("mutation_summary", {})
        if mutation_summary:
            successful_mutations = [
                k for k, v in mutation_summary.items() if v.get("success_rate", 0) > 0
            ]
            if successful_mutations:
                description_parts.append(
                    f"Successful mutation strategies: {', '.join(successful_mutations)}"
                )

        # AI insights
        insights = best.get("insights", "")
        if insights and len(insights) > 50:  # Only include if substantial
            description_parts.append(insights)

        return " | ".join(description_parts)

    def _get_evolution_steps(
        self, all_results: List[Dict], discovery: Dict
    ) -> List[Dict]:
        """Extract evolutionary steps from discovery results with reasoning."""
        if not all_results:
            return []

        steps = []

        # Step 1: Initial exploration phase
        initial_results = [r for r in all_results if r.get("iteration", 0) < 3]
        if initial_results:
            initial_methods = [
                r.get("generation_method", "from_scratch") for r in initial_results
            ]
            from_scratch_count = initial_methods.count("from_scratch")

            steps.append(
                {
                    "title": "Initial Material Generation",
                    "description": f"Generated {len(initial_results)} initial material candidates using AI-driven hypothesis generation. Started with {from_scratch_count} from-scratch generations.",
                    "reasoning": "System begins with broad exploration to establish baseline materials and understand the chemical space, building up a database of candidates for future mutation operations.",
                }
            )

        # Step 2: Transition to mutation-based discovery
        mutation_results = [
            r for r in all_results if r.get("generation_method") == "mutation"
        ]
        if mutation_results:
            first_mutation_iter = min(r.get("iteration", 0) for r in mutation_results)

            steps.append(
                {
                    "title": "Transition to Evolutionary Optimization",
                    "description": f"Began intelligent material mutations at iteration {first_mutation_iter}. Applied {len(mutation_results)} mutation operations to promising parent materials.",
                    "reasoning": "Once baseline materials were established, the system shifted to evolutionary improvement by mutating high-scoring candidates rather than random generation, enabling more targeted optimization.",
                }
            )

        # Step 3: Strategy refinement based on success rates
        mutation_summary = discovery.get("mutation_summary", {})
        if mutation_summary:
            successful_mutations = [
                k for k, v in mutation_summary.items() if v.get("success_rate", 0) > 0.3
            ]
            total_mutations = sum(
                v.get("total_attempts", 0) for v in mutation_summary.values()
            )

            if successful_mutations:
                steps.append(
                    {
                        "title": "Strategy Refinement and Learning",
                        "description": f'Identified {len(successful_mutations)} effective mutation strategies: {", ".join(successful_mutations)}. Applied {total_mutations} total mutations with adaptive strategy selection.',
                        "reasoning": "The system learned which mutation types were most effective for the target properties, focusing computational resources on the most promising transformation strategies while discarding unsuccessful approaches.",
                    }
                )

        # Step 4: Score progression analysis
        scores = [r.get("score", 0) for r in all_results if r.get("score") is not None]
        if len(scores) > 1:
            initial_score = scores[0]
            final_score = max(scores)
            improvement = (
                ((final_score - initial_score) / initial_score * 100)
                if initial_score > 0
                else 0
            )

            # Find when the best material was discovered
            best_score = max(scores)
            best_iter = next(
                (
                    r.get("iteration", 0)
                    for r in all_results
                    if r.get("score") == best_score
                ),
                0,
            )

            steps.append(
                {
                    "title": "Performance Optimization Convergence",
                    "description": f"Achieved {improvement:.1f}% improvement from initial score ({initial_score:.3f}) to final best ({final_score:.3f}). Best material discovered at iteration {best_iter}.",
                    "reasoning": "The evolutionary process successfully optimized target properties through iterative refinement, with the AI learning to generate progressively better materials by leveraging successful mutation patterns and chemical insights.",
                }
            )

        # Step 5: Chemical space exploration analysis
        all_compositions = [
            r.get("composition", "") for r in all_results if r.get("composition")
        ]
        unique_elements = set()
        for comp in all_compositions:
            # Extract elements from composition strings (simplified)
            import re

            elements = re.findall(r"[A-Z][a-z]?", comp)
            unique_elements.update(elements)

        if len(unique_elements) > 3:  # Only add if we explored diverse chemistry
            steps.append(
                {
                    "title": "Chemical Space Diversification",
                    "description": f"Explored {len(unique_elements)} different elements across {len(set(all_compositions))} unique compositions, systematically mapping the rare-earth-free magnetic material space.",
                    "reasoning": "Comprehensive exploration of chemical diversity ensures the discovery process doesn't get trapped in local optima and identifies the most promising regions of chemical space for permanent magnet applications.",
                }
            )

        return steps
