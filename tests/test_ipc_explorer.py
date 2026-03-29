"""Tests for IPCExplorer."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ipc_skill import IPCExplorer, IPCSkillConfig
from ipc_skill.graph_client import GraphClient
from ipc_skill.token_manager import TokenManager


def _make_ipc(config: IPCSkillConfig) -> tuple[IPCExplorer, MagicMock]:
    mock_tm = MagicMock(spec=TokenManager)
    mock_graph = MagicMock(spec=GraphClient)
    ipc = IPCExplorer(config, token_manager=mock_tm, graph_client=mock_graph)
    return ipc, mock_graph


class TestListManagedDevices:
    def test_returns_all_devices_single_page(self, config: IPCSkillConfig):
        ipc, mock_graph = _make_ipc(config)
        devices = [{"id": "d1"}, {"id": "d2"}]
        mock_graph.get.return_value = {"value": devices}

        result = ipc.list_managed_devices()

        assert result == devices

    def test_paginates_using_next_link(self, config: IPCSkillConfig):
        ipc, mock_graph = _make_ipc(config)
        page1 = {
            "value": [{"id": "d1"}],
            "@odata.nextLink": "https://graph.microsoft.com/beta/deviceManagement/managedDevices?$skiptoken=abc",
        }
        page2 = {"value": [{"id": "d2"}]}
        mock_graph.get.side_effect = [page1, page2]

        result = ipc.list_managed_devices()

        assert len(result) == 2
        assert mock_graph.get.call_count == 2

    def test_applies_filter_and_select(self, config: IPCSkillConfig):
        ipc, mock_graph = _make_ipc(config)
        mock_graph.get.return_value = {"value": []}

        ipc.list_managed_devices(
            filter_query="managedDeviceOwnerType eq 'company'",
            select=["deviceName", "operatingSystem"],
        )

        _, call_kwargs = mock_graph.get.call_args
        params = call_kwargs.get("params", {})
        assert params["$filter"] == "managedDeviceOwnerType eq 'company'"
        assert "deviceName" in params["$select"]


class TestGetManagedDevice:
    def test_calls_correct_endpoint(self, config: IPCSkillConfig):
        ipc, mock_graph = _make_ipc(config)
        mock_graph.get.return_value = {"id": "device-123", "deviceName": "LAPTOP-01"}

        result = ipc.get_managed_device("device-123")

        mock_graph.get.assert_called_once_with("/deviceManagement/managedDevices/device-123")
        assert result["deviceName"] == "LAPTOP-01"


class TestListDeviceInventoryCategories:
    def test_returns_categories(self, config: IPCSkillConfig):
        ipc, mock_graph = _make_ipc(config)
        mock_graph.get.return_value = {"value": [{"id": "hardware"}, {"id": "software"}]}

        result = ipc.list_device_inventory_categories("device-123")

        mock_graph.get.assert_called_once_with(
            "/deviceManagement/managedDevices('device-123')/deviceInventories"
        )
        assert len(result) == 2
        assert result[0]["id"] == "hardware"

    def test_returns_empty_list_when_no_categories(self, config: IPCSkillConfig):
        ipc, mock_graph = _make_ipc(config)
        mock_graph.get.return_value = {"value": []}

        result = ipc.list_device_inventory_categories("device-123")

        assert result == []

    def test_returns_empty_list_when_response_is_none(self, config: IPCSkillConfig):
        ipc, mock_graph = _make_ipc(config)
        mock_graph.get.return_value = None

        result = ipc.list_device_inventory_categories("device-123")

        assert result == []


class TestGetDeviceInventory:
    def test_calls_correct_endpoint_with_expand(self, config: IPCSkillConfig):
        ipc, mock_graph = _make_ipc(config)
        mock_graph.get.return_value = {"id": "hardware", "instances": []}

        result = ipc.get_device_inventory("device-123", "hardware")

        call_args, call_kwargs = mock_graph.get.call_args
        assert call_args[0] == "/deviceManagement/managedDevices('device-123')/deviceInventories('hardware')"
        assert "$expand" in call_kwargs.get("params", {})
        assert result["id"] == "hardware"

    def test_returns_empty_dict_when_response_is_none(self, config: IPCSkillConfig):
        ipc, mock_graph = _make_ipc(config)
        mock_graph.get.return_value = None

        result = ipc.get_device_inventory("device-123", "hardware")

        assert result == {}

    def test_returns_instances(self, config: IPCSkillConfig):
        ipc, mock_graph = _make_ipc(config)
        mock_graph.get.return_value = {
            "id": "software",
            "instances": [{"id": "app1", "displayName": "My App", "values": []}],
        }

        result = ipc.get_device_inventory("device-123", "software")

        assert len(result["instances"]) == 1
        assert result["instances"][0]["displayName"] == "My App"
