"""Material registry for tracking materials and caching."""

from typing import Dict, Optional

from ..data.models import Material, MaterialProperties
from ..utils.logging import get_logger

logger = get_logger("registry")


class MaterialRegistry:
    """Registry for tracking materials and caching computed properties."""

    def __init__(self) -> None:
        self._materials: Dict[str, Material] = {}
        self._properties_cache: Dict[str, MaterialProperties] = {}

    def register(self, material: Material) -> None:
        self._materials[material.material_id] = material
        logger.debug(
            f"Registered material: {material.material_id} ({material.composition})"
        )

    def get(self, material_id: str) -> Optional[Material]:
        return self._materials.get(material_id)

    def __contains__(self, material_id: str) -> bool:
        return material_id in self._materials

    def __getitem__(self, material_id: str) -> Material:
        return self._materials[material_id]

    def values(self):
        return self._materials.values()

    def cache_properties(self, file_id: str, properties: MaterialProperties) -> None:
        self._properties_cache[file_id] = properties
        logger.debug(f"Cached properties for file: {file_id}")

    def get_cached_properties(self, file_id: str) -> Optional[MaterialProperties]:
        return self._properties_cache.get(file_id)
