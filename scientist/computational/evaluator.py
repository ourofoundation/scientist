"""Material property evaluation using computational tools."""

from typing import Dict, Any, Optional
from ..data.models import Material, MaterialProperties


class MaterialEvaluator:
    """Handles evaluation of material properties using computational tools."""

    def __init__(self, computational_tools):
        """Initialize evaluator with computational tools.

        Args:
            computational_tools: Instance of ComputationalTools for property evaluation
        """
        self.tools = computational_tools

    def evaluate_properties(self, material: Material) -> MaterialProperties:
        """Evaluate material properties using computational tools.

        Args:
            material: Material to evaluate

        Returns:
            Computed material properties
        """
        return self.tools.evaluate_material_properties(material)

    def interpret_results(
        self,
        material: Material,
        properties: MaterialProperties,
        targets: Dict[str, Any],
        interpretation_module,
    ) -> Any:
        """Interpret computational results and extract insights.

        Args:
            material: The material that was evaluated
            properties: Computed properties
            targets: Target property values
            interpretation_module: DSPy module for interpretation

        Returns:
            Interpretation results with insights and analysis
        """
        mutation_info = "none"
        parent_props = "none"

        if material.generation_method == "mutation" and material.parent_material_id:
            parent_material = self.tools.material_registry.get(
                material.parent_material_id
            )
            if parent_material and material.mutation_history:
                last_mutation = material.mutation_history[-1]
                mutation_info = f"{last_mutation.mutation_type} with params {last_mutation.parameters}"
                if hasattr(parent_material, "predicted_properties"):
                    import json

                    parent_props = json.dumps(parent_material.predicted_properties)

        import json

        return interpretation_module(
            material=material.to_json(),
            # material_properties=json.dumps({"material": properties.__dict__}),
            target_properties=json.dumps(targets),
            mutation_applied=mutation_info,
            parent_properties=parent_props,
        )
