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
from urllib.parse import quote
from typing import Optional

from .config import IPCSkillConfig
from .graph_client import GraphClient
from .graph_client import GraphAPIError
from .token_manager import TokenManager

logger = logging.getLogger(__name__)

_INVENTORY_EXPAND = "instances"
_INVENTORY_EXPAND_WITH_PROPERTIES = "instances($expand=Microsoft.Graph.deviceInventorySimpleItem/properties)"


def _camel_to_title(name: str) -> str:
    """Convert camelCase/PascalCase to Title Case: 'cycleCount' → 'Cycle Count'."""
    spaced = re.sub(r"(?<=[a-z0-9])([A-Z])", r" \1", name)
    return spaced.title()


def _clean_instance(instance: dict) -> dict:
    """Strip OData metadata and convert camelCase keys to friendly Title Case names."""
    cleaned: dict = {}

    for k, v in instance.items():
        if k.startswith("@"):
            continue

        if k == "properties" and isinstance(v, list):
            for prop in v:
                if not isinstance(prop, dict):
                    continue
                prop_name = (
                    prop.get("displayName")
                    or prop.get("name")
                    or prop.get("propertyName")
                    or prop.get("id")
                )
                prop_value = (
                    prop.get("value")
                    if "value" in prop
                    else prop.get("propertyValue")
                )
                if prop_name:
                    cleaned.setdefault(_camel_to_title(str(prop_name)), prop_value)
            continue

        cleaned[_camel_to_title(k) if k != "id" else "Instance Name"] = v

    # Some inventory rows come back as a compact single "id" string with
    # semicolon-delimited key=value pairs (for example from simple item types).
    parsed = _parse_embedded_fields(cleaned.get("Instance Name", ""))
    for key, value in parsed.items():
        cleaned.setdefault(key, value)

    return cleaned


def _parse_embedded_fields(instance_name: str) -> dict:
    """Parse key=value pairs embedded in a simple instance id string."""
    if not isinstance(instance_name, str) or "=" not in instance_name:
        return {}

    parsed: dict = {}
    for part in instance_name.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if not key:
            continue
        parsed[_camel_to_title(key)] = value.strip()
    return parsed


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
        instances = self._get_inventory_instances(device_id, category)

        hydrated: list[dict] = []
        for inst in instances:
            hydrated.append(self._hydrate_simple_instance(device_id, category, inst))

        return [_clean_instance(inst) for inst in hydrated]

    def _get_inventory_instances(self, device_id: str, category: str) -> list[dict]:
        """Fetch inventory instances, preferring the direct instances endpoint."""
        try:
            response = self._graph.get(
                f"/deviceManagement/managedDevices('{device_id}')/deviceInventories('{category}')/instances"
            )
            if response and isinstance(response, dict) and isinstance(response.get("value"), list):
                return response.get("value", [])
        except GraphAPIError as exc:
            # Some tenants reject this route for deviceInventories; fall back to
            # the parent endpoint with nested instance expansion.
            if exc.status_code not in (400, 404):
                raise

        # Fallback for tenants where only parent + $expand is available.
        try:
            fallback = self._graph.get(
                f"/deviceManagement/managedDevices('{device_id}')/deviceInventories('{category}')",
                params={"$expand": _INVENTORY_EXPAND_WITH_PROPERTIES},
            )
        except GraphAPIError:
            fallback = self._graph.get(
                f"/deviceManagement/managedDevices('{device_id}')/deviceInventories('{category}')",
                params={"$expand": _INVENTORY_EXPAND},
            )
        if not fallback:
            return []
        return fallback.get("instances", [])

    def _hydrate_simple_instance(self, device_id: str, category: str, instance: dict) -> dict:
        """Try to expand id-only rows by querying the per-instance endpoint."""
        non_meta_keys = [k for k in instance.keys() if not k.startswith("@")]
        if non_meta_keys != ["id"]:
            return instance

        instance_id = instance.get("id")
        if not isinstance(instance_id, str) or not instance_id:
            return instance

        try:
            detail = self._graph.get(
                f"/deviceManagement/managedDevices('{device_id}')/deviceInventories('{category}')/instances('{quote(instance_id, safe='')}')"
            )
            if (
                isinstance(detail, dict)
                and "id" in detail
                and any(not k.startswith("@") and k != "id" for k in detail.keys())
            ):
                return detail
        except GraphAPIError:
            # Some categories only support list-level instances.
            pass

        return instance
