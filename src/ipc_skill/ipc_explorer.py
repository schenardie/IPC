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
from collections.abc import Callable
from urllib.parse import quote
from typing import Optional

from .config import IPCSkillConfig
from .graph_client import GraphClient
from .graph_client import GraphAPIError
from .token_manager import TokenManager

logger = logging.getLogger(__name__)

_INVENTORY_EXPAND = "instances"
_INVENTORY_EXPAND_WITH_PROPERTIES = "instances($expand=Microsoft.Graph.deviceInventorySimpleItem/properties)"
_SOFTWARE_INVENTORY_CATEGORY = "ApplicationProperties"


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

    def get_software_inventory(self, device_id: str) -> list[dict]:
        """Return cleaned software (application) inventory for a device.

        Uses the ``ApplicationProperties`` category with the nested
        ``$expand=instances($expand=Microsoft.Graph.deviceInventorySimpleItem/properties)``
        query directly, bypassing the ``/instances`` sub-endpoint which is not
        supported for this category.

        Parameters
        ----------
        device_id:
            The Intune managed device GUID.

        Returns
        -------
        list[dict]
            One dict per installed application, keys are friendly names like
            ``"Display Name"``, ``"Version"``, etc.
        """
        response = self._graph.get(
            f"/deviceManagement/managedDevices('{device_id}')/deviceInventories('{_SOFTWARE_INVENTORY_CATEGORY}')",
            params={"$expand": _INVENTORY_EXPAND_WITH_PROPERTIES},
        )
        instances = response.get("instances", []) if response else []
        return [_clean_instance(inst) for inst in instances]

    def get_inventory_batch(
        self,
        device_ids: list[str],
        categories: list[str],
        *,
        on_chunk: Optional[Callable[[int, int], None]] = None,
    ) -> dict[str, dict[str, list[dict]]]:
        """Fetch inventory for multiple devices and categories using Graph batching.

        All (device × category) combinations are issued as a single set of
        batched requests (up to 20 per HTTP call). Id-only instances are
        resolved in a second batch round-trip. Throttled requests are
        automatically retried with the ``Retry-After`` delay.

        Parameters
        ----------
        device_ids:
            List of Intune managed device GUIDs.
        categories:
            List of inventory category IDs (e.g. ``["battery", "diskDrive"]``).
        on_chunk:
            Optional progress callback invoked after each batch chunk of 20
            requests completes. Called as ``on_chunk(done, total)``.

        Returns
        -------
        dict[str, dict[str, list[dict]]]
            ``{device_id: {category: [cleaned_instances]}}``.
            Devices or categories that returned 400/404 are omitted.
        """
        if not device_ids or not categories:
            return {}

        # Phase 1 — fetch all (device × category) pairs in one batched call.
        requests_list: list[dict] = [
            {
                "id": f"{device_id}||{category}",
                "method": "GET",
                "url": (
                    f"/deviceManagement/managedDevices('{device_id}')"
                    f"/deviceInventories('{category}')"
                    f"?$expand={_INVENTORY_EXPAND_WITH_PROPERTIES}"
                ),
            }
            for device_id in device_ids
            for category in categories
        ]

        batch_results = self._graph.batch(requests_list, on_chunk=on_chunk)

        # Phase 2 — extract instances; collect id-only rows for hydration.
        raw: dict[str, dict[str, list[dict]]] = {}
        hydration_requests: list[dict] = []

        for composite_id, result in batch_results.items():
            if "||" not in composite_id:
                continue
            device_id, category = composite_id.split("||", 1)
            status = result["status"]
            body = result["body"]

            if status in (400, 404):
                continue
            if not (200 <= status < 300):
                logger.warning("Inventory %s/%s: unexpected status %d", device_id, category, status)
                continue

            instances = body.get("instances", [])
            raw.setdefault(device_id, {})[category] = instances

            for inst in instances:
                non_meta = [k for k in inst if not k.startswith("@")]
                if non_meta == ["id"] and isinstance(inst.get("id"), str):
                    hydration_requests.append({
                        "id": f"{device_id}||{category}||{inst['id']}",
                        "method": "GET",
                        "url": (
                            f"/deviceManagement/managedDevices('{device_id}')"
                            f"/deviceInventories('{category}')"
                            f"/instances('{quote(inst['id'], safe='')}')"
                        ),
                    })

        # Phase 3 — batch-hydrate id-only instances (if any).
        if hydration_requests:
            hydration_results = self._graph.batch(hydration_requests)
            for composite_id, result in hydration_results.items():
                parts = composite_id.split("||", 2)
                if len(parts) != 3:
                    continue
                device_id, category, inst_id = parts
                if not (200 <= result["status"] < 300):
                    continue
                body = result["body"]
                if not body or "id" not in body:
                    continue
                instances = raw.get(device_id, {}).get(category, [])
                for i, inst in enumerate(instances):
                    if inst.get("id") == inst_id:
                        instances[i] = body
                        break

        return {
            device_id: {
                cat: [_clean_instance(inst) for inst in insts]
                for cat, insts in cats.items()
            }
            for device_id, cats in raw.items()
        }

    def get_software_inventory_batch(
        self,
        device_ids: list[str],
        *,
        on_chunk: Optional[Callable[[int, int], None]] = None,
    ) -> dict[str, list[dict]]:
        """Fetch software inventory for multiple devices using Graph batching.

        Parameters
        ----------
        device_ids:
            List of Intune managed device GUIDs.
        on_chunk:
            Optional progress callback invoked after each batch chunk of 20
            requests completes. Called as ``on_chunk(done, total)``.

        Returns
        -------
        dict[str, list[dict]]
            ``{device_id: [app_dicts]}``.
            Devices that returned 400/404 are omitted.
        """
        if not device_ids:
            return {}

        requests_list = [
            {
                "id": device_id,
                "method": "GET",
                "url": (
                    f"/deviceManagement/managedDevices('{device_id}')"
                    f"/deviceInventories('{_SOFTWARE_INVENTORY_CATEGORY}')"
                    f"?$expand={_INVENTORY_EXPAND_WITH_PROPERTIES}"
                ),
            }
            for device_id in device_ids
        ]

        batch_results = self._graph.batch(requests_list, on_chunk=on_chunk)

        output: dict[str, list[dict]] = {}
        for device_id, result in batch_results.items():
            status = result["status"]
            body = result["body"]
            if status in (400, 404):
                continue
            if not (200 <= status < 300):
                logger.warning("Software inventory %s: unexpected status %d", device_id, status)
                continue
            instances = body.get("instances", [])
            output[device_id] = [_clean_instance(inst) for inst in instances]

        return output

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
