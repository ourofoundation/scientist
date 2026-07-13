"""Ouro API client wrapper for computational materials science."""

import os
import tempfile
from functools import lru_cache
from typing import Dict, List, Optional, Any

from ouro import Ouro
import dotenv

from ..utils.logging import get_logger

dotenv.load_dotenv(override=True)

logger = get_logger("ouro_client")


class OuroClient:
    """Wrapper for Ouro API interactions."""

    def __init__(
        self,
        team_id: str,
        visibility: str = "private",
        post_id: Optional[str] = None,
    ) -> None:
        """Initialize Ouro client.

        Args:
            team_id: Ouro team ID for asset management
            visibility: Asset visibility setting
            post_id: Optional parent post ID for asset parenting
        """
        self.ouro = Ouro(api_key=os.getenv("OURO_API_KEY"))
        self.team_id = team_id
        self.visibility = visibility
        self.post_id = post_id

    @lru_cache(maxsize=512)
    def get_compatible_space_groups(self, formula: str) -> List[int]:
        """Return compatible space groups for a composition.

        Args:
            formula: Chemical formula

        Returns:
            List of compatible space group numbers
        """
        logger.debug(f"Fetching compatible space groups for {formula}")
        action = self.ouro.routes.execute(
            "44aac843-c704-4c1e-b159-4aac4036cb72",
            query={"formula": formula},
            raise_on_error=True,
        )
        compatible = action.final_data
        return [int(g["number"]) for g in compatible.get("compatible_space_groups", [])]

    def generate_crystal(
        self,
        formula: str,
        space_group: Optional[int] = None,
        num_crystals: int = 50,
        optimize_geometry: bool = True,
    ) -> Dict[str, Any]:
        """Generate crystal structure using Ouro crystal generator.

        Args:
            formula: Chemical formula
            space_group: Space group number (optional)
            num_crystals: Number of crystal candidates to generate
            optimize_geometry: Whether to optimize geometry

        Returns:
            Response containing generated structure file
        """
        logger.info(f"Generating crystal via Ouro: {formula} (SG: {space_group})")
        action = self.ouro.routes.execute(
            "41a7d248-1a7f-43c5-b41d-34096c2e1c9c",
            body={
                "formula": formula,
                "space_group": space_group,
                "num_crystals": num_crystals,
                "optimize_geometry": optimize_geometry,
            },
            output={"team_id": self.team_id},
            raise_on_error=True,
        )
        return action.final_data

    def upload_file(
        self,
        file_path: str,
        name: str,
        description: str,
    ) -> Any:
        """Upload a file to Ouro.

        Args:
            file_path: Path to local file
            name: File name in Ouro
            description: File description

        Returns:
            Uploaded file object
        """
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
        """Upload CIF content as a file to Ouro.

        Args:
            cif_content: CIF file content as string
            name: File name
            description: File description

        Returns:
            Uploaded file object
        """
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
        """Retrieve a file from Ouro.

        Args:
            file_id: Ouro file ID

        Returns:
            File object
        """
        return self.ouro.files.retrieve(file_id)

    def execute_route(
        self,
        route_name: str,
        asset_id: str,
        body: Optional[Dict[str, Any]] = None,
        timeout: int = 900,
        poll_timeout: int = 60 * 60 * 3,
    ) -> Dict[str, Any]:
        """Execute an Ouro route with a file asset.

        Args:
            route_name: Route identifier
            asset_id: Input asset ID
            body: Optional body parameters for the route
            timeout: Request timeout in seconds
            poll_timeout: Polling timeout in seconds

        Returns:
            Route response
        """
        action = self.ouro.routes.execute(
            route_name,
            input_assets={"file": str(asset_id)},
            body=body,
            output={"team_id": self.team_id},
            timeout=timeout,
            wait=True,
            poll_timeout=poll_timeout,
            raise_on_error=True,
        )
        return action.final_data

    def use_route(
        self,
        route_name: str,
        asset_id: str,
        body: Optional[Dict[str, Any]] = None,
        timeout: int = 900,
        poll_timeout: int = 60 * 60 * 3,
    ) -> Dict[str, Any]:
        """Deprecated compatibility wrapper for :meth:`execute_route`."""
        return self.execute_route(
            route_name,
            asset_id,
            body=body,
            timeout=timeout,
            poll_timeout=poll_timeout,
        )
