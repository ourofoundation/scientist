"""Ouro API client wrapper for computational materials science."""

from __future__ import annotations

import io
import json
import os
import tempfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from ouro import Ouro
import dotenv

from ..utils.logging import get_logger

dotenv.load_dotenv(override=True)

logger = get_logger("ouro_client")

# Hosted GGen routes (materials-science team)
GGEN_EXPLORE_ROUTE = "mmoderwell/explore-a-chemical-system-with-ggen"
GGEN_EXPORT_ROUTE = "mmoderwell/export-candidate-cifs"


class OuroClient:
    """Wrapper for Ouro API interactions."""

    def __init__(
        self,
        team_id: str,
        visibility: str = "private",
        post_id: Optional[str] = None,
    ) -> None:
        api_key = os.getenv("OURO_API_KEY")
        base_url = os.getenv("OURO_BACKEND_URL") or os.getenv("OURO_BASE_URL")
        self.ouro = Ouro(api_key=api_key, base_url=base_url)
        self.team_id = team_id
        self.visibility = visibility
        self.post_id = post_id
        self._charges: List[Dict[str, Any]] = []

    def _meter(self, route: str, action) -> None:
        """Record what a route run cost and how long it took."""
        usage = action.usage_record or {}
        sats = sum(
            -int(c.get("value") or 0)
            for c in action.btc_charges or []
            if c.get("type") == "route_usage"
        )
        seconds = 0.0
        if action.started_at and action.finished_at:
            seconds = (action.finished_at - action.started_at).total_seconds()
        self._charges.append(
            {
                "route": route,
                "action_id": str(action.id) if action.id else None,
                "status": str(getattr(action.status, "value", action.status)),
                "cost_usd": (usage.get("total_cents") or 0) / 100,
                "cost_sats": sats,
                "seconds": round(seconds, 1),
            }
        )

    def missing_routes(self, routes: List[str]) -> List[str]:
        """Routes that do not resolve on Ouro (renamed, removed, or no access)."""
        missing = []
        for route in routes:
            try:
                self.ouro.routes.retrieve(route)
            except Exception as e:
                logger.warning(f"Route {route} is not available: {e}")
                missing.append(route)
        return missing

    def drain_charges(self) -> List[Dict[str, Any]]:
        """Route charges recorded since the last call."""
        charges, self._charges = self._charges, []
        return charges

    def explore_chemical_system(
        self,
        system: str,
        *,
        max_atoms: int = 16,
        min_atoms: int = 2,
        num_trials: int = 10,
        e_above_hull: float = 0.15,
        max_stoichiometries: Optional[int] = 100,
        crystal_systems: Optional[List[str]] = None,
        min_fraction: Optional[Dict[str, float]] = None,
        max_fraction: Optional[Dict[str, float]] = None,
        skip_existing: bool = False,
        require_all_elements: bool = False,
        include_phase_diagram: bool = True,
        poll_timeout: int = 60 * 60 * 4,
    ) -> Dict[str, Any]:
        """Run hosted GGen exploration and return summary + output assets.

        Returns a dict with:
          - summary: exploration summary including stable_phases
          - action_id: Ouro action id
          - candidate_cifs: optional file asset for the CIF zip
          - report / phase_diagram / summary_file: other output assets
        """
        body: Dict[str, Any] = {
            "system": system,
            "max_atoms": max_atoms,
            "min_atoms": min_atoms,
            "num_trials": num_trials,
            "e_above_hull": e_above_hull,
            "max_stoichiometries": max_stoichiometries,
            "skip_existing": skip_existing,
            "require_all_elements": require_all_elements,
            "include_phase_diagram": include_phase_diagram,
        }
        if crystal_systems:
            body["crystal_systems"] = crystal_systems
        if min_fraction:
            body["min_fraction"] = min_fraction
        if max_fraction:
            body["max_fraction"] = max_fraction

        logger.info(f"Starting hosted GGen exploration: {system}")
        # Async route: Prefer respond-async so we don't hold an HTTP connection
        # open for the whole GPU job (that hits httpx read timeout ~10min).
        action = self.ouro.routes.execute(
            GGEN_EXPLORE_ROUTE,
            body=body,
            output={"team_id": self.team_id},
            wait=False,
            raise_on_error=False,
        )
        logger.info(
            f"Exploration queued (action={action.id}, status={action.status}); polling…"
        )
        # Don't raise on error: GGen can succeed while Ouro fails materializing
        # partial side-effect assets; the webhook body still carries the summary.
        action = self.ouro.routes.poll_action(
            str(action.id),
            timeout=poll_timeout,
            raise_on_error=False,
        )

        self._meter(GGEN_EXPLORE_ROUTE, action)
        summary = self.extract_explore_summary(action)
        if not summary.get("stable_phases") and action.status != "success":
            raise RuntimeError(
                f"GGen exploration failed for {system} "
                f"(action={action.id}, status={action.status})"
            )

        assets = self._keyed_output_assets(action)
        logger.info(
            f"Exploration complete for {system}: "
            f"{len(summary.get('stable_phases', []))} near-hull phases"
        )
        return {
            "summary": summary,
            "action_id": str(action.id) if action.id else None,
            "candidate_cifs": assets.get("candidate_cifs"),
            "report": assets.get("report"),
            "phase_diagram": assets.get("phase_diagram"),
            "summary_file": assets.get("summary"),
        }

    def export_candidates(
        self,
        system: str,
        *,
        max_e_above_hull: float = 0.15,
        crystal_systems: Optional[List[str]] = None,
        dynamically_stable_only: bool = False,
        include_metadata: bool = True,
        structure_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Export near-hull CIFs from the hosted GGen database.

        Returns dict with `structures` (from metadata.json) and `cifs`
        mapping filename → cif content string.
        """
        body: Dict[str, Any] = {
            "system": system,
            "max_e_above_hull": max_e_above_hull,
            "dynamically_stable_only": dynamically_stable_only,
            "include_metadata": include_metadata,
        }
        if crystal_systems:
            body["crystal_systems"] = crystal_systems
        if structure_ids:
            body["structure_ids"] = structure_ids

        logger.info(f"Exporting GGen candidates for {system}")
        action = self.ouro.routes.execute(
            GGEN_EXPORT_ROUTE,
            body=body,
            output={"team_id": self.team_id},
            wait=True,
            raise_on_error=True,
        )
        self._meter(GGEN_EXPORT_ROUTE, action)

        file_asset = self._primary_file_asset(action)
        if not file_asset or not file_asset.get("id"):
            raise RuntimeError(f"Export for {system} returned no file asset")

        return self.parse_candidate_zip(file_asset["id"])

    def download_file_bytes(self, file_id: str) -> bytes:
        """Download raw bytes for an Ouro file asset."""
        file_obj = self.ouro.files.retrieve(file_id)
        data = file_obj.read_data()
        resp = requests.get(data.url, timeout=120)
        resp.raise_for_status()
        return resp.content

    def download_file_text(self, file_id: str) -> str:
        return self.download_file_bytes(file_id).decode("utf-8")

    def upload_file(
        self,
        file_path: str,
        name: str,
        description: str,
    ) -> Any:
        logger.debug(f"Uploading file: {name}")
        return self.ouro.files.create(
            file_path=file_path,
            name=name,
            description=description,
            visibility=self.visibility,
            team_id=self.team_id,
            parent_id=self.post_id,
        )

    def upload_cif_content(
        self,
        cif_content: str,
        name: str,
        description: str,
    ) -> Any:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".cif", delete=False
        ) as temp_file:
            temp_file.write(cif_content)
            temp_file_path = temp_file.name

        try:
            uploaded_file = self.upload_file(temp_file_path, name, description)
            logger.info(f"Uploaded CIF as Ouro file: {uploaded_file.id}")
            return uploaded_file
        finally:
            os.unlink(temp_file_path)

    def retrieve_file(self, file_id: str) -> Any:
        return self.ouro.files.retrieve(file_id)

    def execute_route(
        self,
        route_name: str,
        asset_id: str,
        body: Optional[Dict[str, Any]] = None,
        timeout: int = 900,
        poll_timeout: int = 60 * 60 * 3,
        input_name: str = "file",
    ) -> Dict[str, Any]:
        """Execute an Ouro route with a file asset input.

        Returns ``data`` (the response payload), the ``action_id``, and the
        ids of any ``output_assets`` keyed by output name, so a report can
        link and embed what the route produced.
        """
        # Queue and poll rather than holding one HTTP request open: a slow
        # upstream start (cold DFT container) outlives the gateway timeout.
        started = datetime.now(timezone.utc)
        try:
            action = self.ouro.routes.execute(
                route_name,
                input_assets={input_name: str(asset_id)},
                body=body,
                output={"team_id": self.team_id},
                timeout=timeout,
                wait=False,
                raise_on_error=False,
            )
        except Exception as e:
            # The request can fail after the job was accepted; find it.
            action = self._find_action(route_name, asset_id, started)
            if action is None:
                raise
            logger.warning(f"{route_name}: request failed ({e}); following {action.id}")

        action = self._poll(str(action.id), poll_timeout)
        self._meter(route_name, action)
        if action.is_error:
            raise RuntimeError(
                f"{route_name} failed (action {action.id}): "
                f"{json.dumps(action.response, default=str)[:500]}"
            )
        return {
            "data": action.final_data,
            "action_id": str(action.id) if action.id else None,
            "output_assets": {
                name: {
                    "id": asset.get("id"),
                    "asset_type": asset.get("asset_type"),
                    "name": asset.get("name"),
                }
                for name, asset in self._keyed_output_assets(action).items()
                if asset.get("id")
            },
        }

    def _find_action(self, route_name: str, asset_id: str, since: datetime):
        """The action this client just started on ``asset_id``, if one exists."""
        try:
            page = self.ouro.routes.list_actions(route_name, limit=5)
        except Exception:
            return None
        for action in getattr(page, "data", None) or []:
            inputs = {str(action.input_asset_id)} | {
                str((i.get("asset") or {}).get("id") or i.get("asset_id"))
                for i in action.input_assets or []
            }
            recent = action.created_at and action.created_at >= since - timedelta(
                seconds=30
            )
            if recent and str(asset_id) in inputs:
                return action
        return None

    def _poll(self, action_id: str, timeout: float, interval: float = 10.0):
        """Poll an action to completion, riding out transient API errors."""
        deadline = time.time() + timeout
        failures = 0
        while True:
            try:
                action = self.ouro.routes.retrieve_action(action_id)
                failures = 0
                if action.is_complete:
                    return action
            except Exception as e:
                failures += 1
                if failures >= 10:
                    raise
                logger.warning(f"Polling {action_id} failed ({e}); retrying")
            if time.time() >= deadline:
                raise TimeoutError(
                    f"Action {action_id} did not complete within {timeout:.0f}s"
                )
            time.sleep(interval)

    def _keyed_output_assets(self, action) -> Dict[str, Dict[str, Any]]:
        assets: Dict[str, Dict[str, Any]] = {}
        for output in action.output_assets or []:
            name = output.get("name")
            asset = output.get("asset") or {}
            if name and isinstance(asset, dict):
                assets[name] = asset
        return assets

    def _primary_file_asset(self, action) -> Optional[Dict[str, Any]]:
        assets = self._keyed_output_assets(action)
        if "file" in assets:
            return assets["file"]
        if action.output_asset:
            return action.output_asset.model_dump(mode="json")
        final = action.final_data if isinstance(action.final_data, dict) else {}
        if isinstance(final.get("file"), dict):
            return final["file"]
        return None

    def extract_explore_summary(self, action) -> Dict[str, Any]:
        """Pull exploration summary from a success or failed-but-payload action.

        Prefer ``exploration_summary`` (current GGen webhook shape). Legacy
        actions may still have top-level ``summary``, a materialized summary
        file asset, or a failed-action ``webhook_body`` payload.
        """
        response = action.response if isinstance(action.response, dict) else {}
        for key in ("exploration_summary", "summary"):
            summary = response.get(key)
            if isinstance(summary, dict) and summary.get("stable_phases") is not None:
                return summary

        webhook = response.get("webhook_body") or {}
        if isinstance(webhook, dict):
            nested = webhook.get("response") or {}
            if isinstance(nested, dict):
                for key in ("exploration_summary", "summary"):
                    summary = nested.get(key)
                    if isinstance(summary, dict) and summary.get("stable_phases") is not None:
                        return summary

        return self._load_summary_from_assets(action) or {}

    def _load_summary_from_assets(self, action) -> Optional[Dict[str, Any]]:
        assets = self._keyed_output_assets(action)
        summary_file = assets.get("summary")
        if not summary_file or not summary_file.get("id"):
            return None
        try:
            text = self.download_file_text(summary_file["id"])
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except Exception as e:
            logger.warning(f"Failed to load summary file: {e}")
            return None

    def parse_candidate_zip(self, file_id: str) -> Dict[str, Any]:
        raw = self.download_file_bytes(file_id)
        structures: List[Dict[str, Any]] = []
        cifs: Dict[str, str] = {}

        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = zf.namelist()
            if "metadata.json" in names:
                meta = json.loads(zf.read("metadata.json"))
                structures = meta.get("structures") or []
            for name in names:
                if name.lower().endswith(".cif"):
                    cifs[name] = zf.read(name).decode("utf-8")

        return {"structures": structures, "cifs": cifs, "file_id": file_id}
