"""Measure catalog properties for a candidate structure via Ouro routes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..utils.logging import get_logger
from .catalog import CATALOG, PropertySpec

if TYPE_CHECKING:
    from .ouro_client import OuroClient

logger = get_logger("evaluator")


@dataclass
class Measurement:
    """Result of measuring some properties on one candidate."""

    file_id: Optional[str] = None
    values: Dict[str, Any] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)
    # property → {action_id, route, output_assets} for citing in reports
    artifacts: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class Evaluator:
    """Runs the routes behind catalog properties, in parallel per candidate."""

    def __init__(
        self,
        ouro_client: OuroClient,
        catalog: Optional[Dict[str, PropertySpec]] = None,
    ) -> None:
        self.ouro = ouro_client
        self.catalog = catalog or CATALOG

    def measure(self, candidate: Dict[str, Any], properties: List[str]) -> Measurement:
        """Measure ``properties`` that the candidate doesn't already have."""
        known = candidate.get("properties") or {}
        specs = [
            self.catalog[name]
            for name in properties
            if name in self.catalog
            and self.catalog[name].route
            and known.get(name) is None
        ]
        result = Measurement(file_id=candidate.get("file_id"))
        if not specs:
            return result

        try:
            result.file_id = self._ensure_file(candidate)
        except Exception as e:
            logger.error(f"Upload failed for {candidate['formula']}: {e}")
            result.errors = {s.name: f"structure upload failed: {e}" for s in specs}
            return result

        logger.info(f"Measuring {[s.name for s in specs]} for {candidate['formula']}")
        with ThreadPoolExecutor(max_workers=min(8, len(specs))) as pool:
            outcomes = pool.map(lambda s: self._run(s, result.file_id), specs)

        for spec, (value, error, artifact) in zip(specs, outcomes):
            if artifact:
                result.artifacts[spec.name] = artifact
            if error:
                result.errors[spec.name] = error
            else:
                result.values[spec.name] = value
        if result.errors:
            logger.warning(f"Failed: {list(result.errors)}")
        return result

    def _run(self, spec: PropertySpec, file_id: str):
        try:
            response = self.ouro.execute_route(
                spec.route, file_id, body=spec.body, input_name=spec.input_name
            )
        except Exception as e:
            return None, str(e), None

        artifact = {
            "route": spec.route,
            "action_id": response.get("action_id"),
            "output_assets": response.get("output_assets") or {},
        }
        try:
            return spec.extract(response["data"]), None, artifact
        except (KeyError, TypeError) as e:
            return None, f"unexpected route response: {e!r}", artifact

    def _ensure_file(self, candidate: Dict[str, Any]) -> str:
        if candidate.get("file_id"):
            return candidate["file_id"]
        if not candidate.get("cif"):
            raise ValueError("candidate has no CIF")

        formula = candidate["formula"]
        sg = (candidate.get("properties") or {}).get("space_group")
        parts = [f"Crystal structure for {formula}"]
        if candidate.get("system"):
            parts.append(f"System: {candidate['system']}")
        if sg:
            parts.append(f"Space group: {sg}")
        uploaded = self.ouro.upload_cif_content(
            candidate["cif"],
            name=f"{formula} SG #{sg}" if sg else formula,
            description=" | ".join(parts),
        )
        return str(uploaded.id)
