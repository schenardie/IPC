"""Tests for IPCExplorer."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ipc_skill import IPCExplorer, IPCSkillConfig
from ipc_skill.graph_client import GraphClient
from ipc_skill.graph_client import GraphAPIError
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
        mock_graph.get.return_value = {"value": []}

        result = ipc.get_device_inventory("device-123", "hardware")

        call_args, call_kwargs = mock_graph.get.call_args
        assert call_args[0] == "/deviceManagement/managedDevices('device-123')/deviceInventories('hardware')/instances"
        assert call_kwargs == {}
        assert result == []

    def test_returns_empty_list_when_response_is_none(self, config: IPCSkillConfig):
        ipc, mock_graph = _make_ipc(config)
        mock_graph.get.side_effect = [None, None]

        result = ipc.get_device_inventory("device-123", "hardware")

        assert result == []

    def test_returns_cleaned_instances(self, config: IPCSkillConfig):
        ipc, mock_graph = _make_ipc(config)
        mock_graph.get.return_value = {
            "value": [
                {
                    "id": "{BFD21D0B}\\SurfaceBattery",
                    "cycleCount": 256,
                    "designedCapacity": 47700,
                    "manufacturer": "DYN",
                    "@odata.type": "#microsoft.graph.battery",
                }
            ],
        }

        result = ipc.get_device_inventory("device-123", "battery")

        assert len(result) == 1
        instance = result[0]
        assert instance["Instance Name"] == "{BFD21D0B}\\SurfaceBattery"
        assert instance["Cycle Count"] == 256
        assert instance["Designed Capacity"] == 47700
        assert instance["Manufacturer"] == "DYN"
        assert "@odata.type" not in instance

    def test_strips_odata_fields(self, config: IPCSkillConfig):
        ipc, mock_graph = _make_ipc(config)
        mock_graph.get.return_value = {
            "value": [{"id": "inst1", "@odata.type": "noise", "diskName": "C:"}],
        }

        result = ipc.get_device_inventory("device-123", "diskDrive")

        assert "@odata.context" not in result[0]
        assert "@odata.type" not in result[0]
        assert result[0]["Disk Name"] == "C:"

    def test_parses_key_value_pairs_embedded_in_instance_name(self, config: IPCSkillConfig):
        ipc, mock_graph = _make_ipc(config)
        mock_graph.get.return_value = {
            "value": [
                {
                    "id": "PhysicalProcessorCount=1;ComputerName=CPC-jose-CIWHG6;HardwareModel=Virtual Machine",
                    "@odata.type": "#microsoft.graph.deviceInventorySimpleItem",
                }
            ]
        }

        result = ipc.get_device_inventory("device-123", "SystemInfo")

        assert result[0]["Physical Processor Count"] == "1"
        assert result[0]["Computer Name"] == "CPC-jose-CIWHG6"
        assert result[0]["Hardware Model"] == "Virtual Machine"

    def test_falls_back_when_direct_instances_route_returns_400(self, config: IPCSkillConfig):
        ipc, mock_graph = _make_ipc(config)
        mock_graph.get.side_effect = [
            GraphAPIError(400, "No route", "No method match route template"),
            {
                "id": "SystemInfo",
                "instances": [
                    {
                        "id": "PhysicalProcessorCount=1;ComputerName=CPC-jose-CIWHG6",
                    }
                ],
            },
            GraphAPIError(400, "No route", "No method match route template"),
        ]

        result = ipc.get_device_inventory("device-123", "SystemInfo")

        assert result[0]["Physical Processor Count"] == "1"
        assert result[0]["Computer Name"] == "CPC-jose-CIWHG6"

        first_call_args, _ = mock_graph.get.call_args_list[0]
        second_call_args, second_call_kwargs = mock_graph.get.call_args_list[1]
        assert first_call_args[0].endswith("/deviceInventories('SystemInfo')/instances")
        assert second_call_args[0].endswith("/deviceInventories('SystemInfo')")
        assert "$expand" in second_call_kwargs["params"]
