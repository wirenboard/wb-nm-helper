import argparse
import json
import subprocess
from unittest.mock import MagicMock, Mock, patch

import dbus
import dbusmock
import jsonschema
from dbusmock.templates.networkmanager import (
    CSETTINGS_IFACE,
    MANAGER_IFACE,
    SETTINGS_IFACE,
    SETTINGS_OBJ,
    DeviceState,
)

from wb.nm_helper import nm_helper

from . import connections_settings as connections


class TestNetworkManagerHelperImport(dbusmock.DBusTestCase):
    @classmethod
    def setUpClass(cls):
        cls.start_system_bus()
        cls.system_bus = cls.get_dbus(system_bus=True)

    def setUp(self):
        (self.p_mock, self.obj_networkmanager) = self.spawn_server_template(
            "networkmanager", {"NetworkingEnabled": True}, stdout=subprocess.PIPE
        )
        self.networkmanager_mock = dbus.Interface(self.obj_networkmanager, dbusmock.MOCK_IFACE)
        self.settings = dbus.Interface(
            self.system_bus.get_object(MANAGER_IFACE, SETTINGS_OBJ), SETTINGS_IFACE
        )

    def tearDown(self):
        if self.p_mock:
            self.p_mock.stdout.close()
            self.p_mock.terminate()
            self.p_mock.wait()
            self.p_mock = None

    def test_to_json(self):
        # pylint: disable=R0915

        nm_helper.is_modem_enabled = Mock(return_value=True)

        self.networkmanager_mock.AddEthernetDevice("mock_eth0", "eth0", DeviceState.ACTIVATED)
        self.networkmanager_mock.AddEthernetDevice("mock_eth1", "eth1", DeviceState.ACTIVATED)
        self.networkmanager_mock.AddWiFiDevice("mock_wlan0", "wlan0", DeviceState.ACTIVATED)
        self.networkmanager_mock.AddWiFiDevice("mock_wlan1", "wlan1", DeviceState.ACTIVATED)

        self.settings.AddConnection(connections.ETH0_DBUS_SETTINGS)
        self.settings.AddConnection(connections.ETH1_DBUS_SETTINGS)
        self.settings.AddConnection(connections.GSM_SIM1_DBUS_SETTINGS)
        self.settings.AddConnection(connections.GSM_SIM2_DBUS_SETTINGS)
        self.settings.AddConnection(connections.WB_AP_DBUS_SETTINGS)

        res = nm_helper.to_json(
            args=argparse.Namespace(
                config="tests/data/wb-connection-manager.conf",
                interfaces_conf="tests/data/interfaces",
                no_scan=True,
            )
        )

        with open("../../../wb-network.schema.json", "r", encoding="utf-8") as f:
            schema = json.load(f)

        assert jsonschema.Draft4Validator(schema).is_valid(res)
        assert len(res["data"]["devices"]) == 4
        assert res["data"]["devices"][0]["iface"] == "eth0"
        assert res["data"]["devices"][0]["type"] == "ethernet"
        assert res["data"]["devices"][1]["iface"] == "eth1"
        assert res["data"]["devices"][1]["type"] == "ethernet"
        assert res["data"]["devices"][2]["iface"] == "wlan0"
        assert res["data"]["devices"][2]["type"] == "wifi"
        assert res["data"]["devices"][3]["iface"] == "wlan1"
        assert res["data"]["devices"][3]["type"] == "wifi"
        assert res["ui"]["con_switch"]["debug"] is False
        assert len(res["ui"]["connections"]) == 10
        assert res["ui"]["connections"][0]["802-11-wireless-security"]["security"] == "none"
        assert res["ui"]["connections"][0]["802-11-wireless_mode"] == "ap"
        assert res["ui"]["connections"][0]["802-11-wireless_ssid"] == "WirenBoard-Тест"
        assert res["ui"]["connections"][0]["connection_autoconnect"] is True
        assert res["ui"]["connections"][0]["connection_id"] == "wb-ap"
        assert res["ui"]["connections"][0]["connection_interface-name"] == "wlan0"
        assert res["ui"]["connections"][0]["connection_uuid"] == "d12c8d3c-1abe-4832-9b71-4ed6e3c20885"
        assert res["ui"]["connections"][0]["ipv4"]["address"] == "192.168.42.1"
        assert res["ui"]["connections"][0]["ipv4"]["method"] == "shared"
        assert res["ui"]["connections"][0]["ipv4"]["netmask"] == "255.255.255.0"
        assert res["ui"]["connections"][0]["type"] == "04_nm_wifi_ap"
        assert res["ui"]["connections"][1]["connection_autoconnect"] is True
        assert res["ui"]["connections"][1]["connection_id"] == "wb-eth0"
        assert res["ui"]["connections"][1]["connection_interface-name"] == "eth0"
        assert res["ui"]["connections"][1]["connection_uuid"] == "91f1c71d-2d97-4675-886f-ecbe52b8451e"
        assert res["ui"]["connections"][1]["ipv4"]["method"] == "auto"
        assert res["ui"]["connections"][1]["type"] == "01_nm_ethernet"
        assert res["ui"]["connections"][2]["connection_autoconnect"] is True
        assert res["ui"]["connections"][2]["connection_id"] == "wb-eth1"
        assert res["ui"]["connections"][2]["connection_interface-name"] == "eth1"
        assert res["ui"]["connections"][2]["connection_uuid"] == "c3e38405-9c17-4155-ad70-664311b49066"
        assert res["ui"]["connections"][2]["ipv4"]["method"] == "auto"
        assert res["ui"]["connections"][2]["type"] == "01_nm_ethernet"
        assert res["ui"]["connections"][3]["connection_autoconnect"] is False
        assert res["ui"]["connections"][3]["connection_id"] == "wb-gsm-sim1"
        assert res["ui"]["connections"][3]["connection_uuid"] == "5d4297ba-c319-4c05-a153-17cb42e6e196"
        assert res["ui"]["connections"][3]["gsm_apn"] == ""
        assert res["ui"]["connections"][3]["gsm_sim-slot"] == 1
        assert res["ui"]["connections"][3]["ipv4"]["method"] == "auto"
        assert res["ui"]["connections"][3]["type"] == "02_nm_modem"
        assert res["ui"]["connections"][4]["connection_autoconnect"] is False
        assert res["ui"]["connections"][4]["connection_id"] == "wb-gsm-sim2"
        assert res["ui"]["connections"][4]["connection_uuid"] == "8b9964d4-b8dd-34d3-a3ed-481840bcf8c9"
        assert res["ui"]["connections"][4]["gsm_apn"] == ""
        assert res["ui"]["connections"][4]["gsm_sim-slot"] == 2
        assert res["ui"]["connections"][4]["ipv4"]["method"] == "auto"
        assert res["ui"]["connections"][4]["type"] == "02_nm_modem"
        assert res["ui"]["connections"][5]["allow-hotplug"] is True
        assert res["ui"]["connections"][5]["auto"] is False
        assert res["ui"]["connections"][5]["method"] == "static"
        assert res["ui"]["connections"][5]["mode"] == "inet"
        assert res["ui"]["connections"][5]["name"] == "wlan0"
        assert res["ui"]["connections"][5]["options"]["address"] == "192.168.42.1"
        assert res["ui"]["connections"][5]["options"]["netmask"] == "255.255.255.0"
        assert res["ui"]["connections"][5]["type"] == "static"
        assert res["ui"]["connections"][6]["allow-hotplug"] is True
        assert res["ui"]["connections"][6]["auto"] is True
        assert res["ui"]["connections"][6]["method"] == "dhcp"
        assert res["ui"]["connections"][6]["mode"] == "inet"
        assert res["ui"]["connections"][6]["name"] == "eth0"
        assert res["ui"]["connections"][6]["options"]["hostname"] == "WirenBoard"
        assert res["ui"]["connections"][6]["options"]["pre-up"] == ["wb-set-mac"]
        assert res["ui"]["connections"][6]["type"] == "dhcp"
        assert res["ui"]["connections"][7]["allow-hotplug"] is True
        assert res["ui"]["connections"][7]["auto"] is False
        assert res["ui"]["connections"][7]["method"] == "dhcp"
        assert res["ui"]["connections"][7]["mode"] == "inet"
        assert res["ui"]["connections"][7]["name"] == "eth1"
        assert res["ui"]["connections"][7]["options"]["hostname"] == "WirenBoard"
        assert res["ui"]["connections"][7]["options"]["pre-up"] == [
            "wb-set-mac # comment1",
            "sleep 10   # comment2",
            "#test",
        ]
        assert res["ui"]["connections"][7]["type"] == "dhcp"
        assert res["ui"]["connections"][8]["allow-hotplug"] is True
        assert res["ui"]["connections"][8]["auto"] is False
        assert res["ui"]["connections"][8]["method"] == "dhcp"
        assert res["ui"]["connections"][8]["mode"] == "inet"
        assert res["ui"]["connections"][8]["name"] == "eth2"
        assert res["ui"]["connections"][8]["options"]["hwaddress"] == "94:C6:91:91:4D:5A"
        assert res["ui"]["connections"][8]["type"] == "dhcp"
        assert res["ui"]["connections"][9]["allow-hotplug"] is True
        assert res["ui"]["connections"][9]["auto"] is False
        assert res["ui"]["connections"][9]["method"] == "static"
        assert res["ui"]["connections"][9]["mode"] == "can"
        assert res["ui"]["connections"][9]["name"] == "can0"
        assert res["ui"]["connections"][9]["options"]["bitrate"] == 125000
        assert res["ui"]["connections"][9]["type"] == "can"

    def _test_from_json_gsm_common(self, modem_enabled):
        nm_helper.get_systemd_manager = Mock()
        nm_helper.is_modem_enabled = Mock(return_value=modem_enabled)

        with open("tests/data/ui_without_gsm.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)

        nm_helper.from_json(
            cfg,
            args=argparse.Namespace(
                interfaces_conf="tests/data/non-exist-file",
                dnsmasq_conf="tests/data/dnsmasq.conf",
                hostapd_conf="tests/data/hostapd.conf",
                dry_run=False,
            ),
        )

        new_connections = []
        connections_paths = self.settings.ListConnections()
        for connection_path in connections_paths:
            connection = dbus.Interface(
                self.system_bus.get_object(MANAGER_IFACE, connection_path), CSETTINGS_IFACE
            )
            settings = connection.GetSettings()
            new_connections.append(settings["connection"]["id"])

        return new_connections

    def test_from_json_modem_en(self):
        self.settings.AddConnection(connections.ETH0_DBUS_SETTINGS)
        self.settings.AddConnection(connections.ETH1_DBUS_SETTINGS)
        self.settings.AddConnection(connections.GSM_SIM1_DBUS_SETTINGS)
        self.settings.AddConnection(connections.GSM_SIM2_DBUS_SETTINGS)

        new_connections = self._test_from_json_gsm_common(True)
        assert new_connections == ["wb-eth0", "wb-eth1", "wb-ap"]

    def test_from_json_modem_dis(self):
        self.settings.AddConnection(connections.GSM_SIM1_DBUS_SETTINGS)
        self.settings.AddConnection(connections.GSM_SIM2_DBUS_SETTINGS)

        new_connections = self._test_from_json_gsm_common(False)
        assert new_connections == ["wb-gsm-sim1", "wb-gsm-sim2", "wb-eth0", "wb-eth1", "wb-ap"]


def test_from_json():
    with open("tests/data/ui.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # we need to unset dry_run only for "apply" method
    with patch("wb.nm_helper.network_manager_adapter.NetworkManagerAdapter", MagicMock):
        res = nm_helper.from_json(
            cfg,
            args=argparse.Namespace(
                interfaces_conf="tests/data/interfaces",
                dnsmasq_conf="tests/data/dnsmasq.conf",
                hostapd_conf="tests/data/hostapd.conf",
                dry_run=True,
            ),
        )

        assert len(res["connections"]) == 0
        assert res["debug"] is False
