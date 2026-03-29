"""
IPCSkill – Intune Device Inventory skill.

Provides hardware/software inventory queries via the Intune
managedDevices deviceInventories API.

Tokens are shared with ExplorerSkill (same keyring service name),
so logging in once with either skill grants access to both.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from .config import IPCSkillConfig
from .graph_client import GraphClient
from .token_manager import TokenManager

logger = logging.getLogger(__name__)

_INVENTORY_EXPAND = "instances"


def _camel_to_title(name: str) -> str:
    """Convert camelCase/PascalCase to Title Case: 'cycleCount' → 'Cycle Count'."""
    spaced = re.sub(r"(?<=[a-z0-9])([A-Z])", r" \1", name)
    return spaced.title()


def _clean_instance(instance: dict) -> dict:
    """Strip OData metadata and convert camelCase keys to friendly Title Case names."""
    return {
        (_camel_to_title(k) if k != "id" else "Instance Name"): v
        for k, v in instance.items()
        if not k.startswith("@")
    }


class IPCExplorer:
    """High-level Intune device inventory skill.

    Tokens are stored under the ``explorer-skill`` keyring service so any
    token stored via :class:`explorer_skill.IntuneExplorer` is reused here
    automatically.

    Example
    -------
    >>> from explorer_skill import ExplorerSkillConfig
    >>> from ipc_skill import IPCExplorer
    >>> config = ExplorerSkillConfig()
    >>> ipc = IPCExplorer(config)
    >>> ipc.token_manager.store_token(access_token="<paste token>")
    >>> categories = ipc.list_device_inventory_categories("<device-id>")
    """

    def __init__(
        self,
        config: IPCSkillConfig,
        token_manager: Optional[TokenManager] = None,
        graph_client: Optional[GraphClient] = None,
    ) -> None:
        self._config = config
        self.token_manager: TokenManager = token_manager or TokenManager(config)
        self._graph: GraphClient = graph_client or GraphClient(
            config, token_manager=self.token_manager
        )

    # ------------------------------------------------------------------
    # Managed device helpers (used to resolve device names/IDs)
    # ------------------------------------------------------------------

    def list_managed_devices(
        self,
        filter_query: Optional[str] = None,
        select: Optional[list[str]] = None,
        top: int = 100,
    ) -> list[dict]:
        """List managed devices visible to the authenticated user."""
        params: dict = {"$top": top}
        if filter_query:
            params["$filter"] = filter_query
        if select:
            params["$select"] = ",".join(select)

        results: list[dict] = []
        response = self._graph.get("/deviceManagement/managedDevices", params=params)
        results.extend(response.get("value", []))
        next_link: Optional[str] = response.get("@odata.nextLink")
        while next_link:
            response = self._graph.get(next_link)
            results.extend(response.get("value", []))
            next_link = response.get("@odata.nextLink")
        return results

    def get_managed_device(self, device_id: str) -> dict:
        """Fetch a single managed device by its Intune device ID."""
        return self._graph.get(f"/deviceManagement/managedDevices/{device_id}")

    # ------------------------------------------------------------------
    # Device inventory
    # ------------------------------------------------------------------

    def list_device_inventory_categories(self, device_id: str) -> list[dict]:
        """Return the inventory categories available for a device.

        Parameters
        ----------
        device_id:
            The Intune managed device GUID.

        Returns
        -------
        list[dict]
            Each element has at least an ``id`` field identifying the category.
        """
        response = self._graph.get(
            f"/deviceManagement/managedDevices('{device_id}')/deviceInventories"
        )
        return response.get("value", []) if response else []

    def get_device_inventory(self, device_id: str, category: str) -> list[dict]:
        """Return cleaned inventory instances for a device and category.

        Each instance is a dict with friendly Title Case keys matching the
        column names shown in the Intune admin center. OData metadata fields
        are stripped automatically.

        Parameters
        ----------
        device_id:
            The Intune managed device GUID.
        category:
            Inventory category ID (e.g. ``"battery"``, ``"diskDrive"``).

        Returns
        -------
        list[dict]
            One dict per instance, keys are friendly names like
            ``"Cycle Count"``, ``"Designed Capacity"``, etc.
        """
        response = self._graph.get(
            f"/deviceManagement/managedDevices('{device_id}')/deviceInventories('{category}')",
            params={"$expand": _INVENTORY_EXPAND},
        )
        if not response:
            return []
        return [_clean_instance(inst) for inst in response.get("instances", [])]
