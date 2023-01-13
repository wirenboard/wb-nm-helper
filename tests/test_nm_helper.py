import argparse
import json
import subprocess

import dbus
import dbusmock
from dbusmock.templates.networkmanager import (
    MANAGER_IFACE,
    SETTINGS_IFACE,
    SETTINGS_OBJ,
    DeviceState,
)

from wb.nm_helper.nm_helper import from_json, to_json


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

    def add_wb_eth0(self):
        self.settings.AddConnection(
            dbus.Dictionary(
                {
                    "connection": dbus.Dictionary(
                        {
                            "id": dbus.String("wb-eth0", variant_level=1),
                            "interface-name": dbus.String("eth0", variant_level=1),
                            "type": "802-3-ethernet",
                            "uuid": dbus.String("91f1c71d-2d97-4675-886f-ecbe52b8451e", variant_level=1),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    "ipv4": dbus.Dictionary(
                        {"method": dbus.String("auto", variant_level=1)}, signature=dbus.Signature("sv")
                    ),
                },
                signature=dbus.Signature("sa{sv}"),
            )
        )

    def add_wb_eth1(self):
        self.settings.AddConnection(
            dbus.Dictionary(
                {
                    "connection": dbus.Dictionary(
                        {
                            "id": dbus.String("wb-eth1", variant_level=1),
                            "interface-name": dbus.String("eth1", variant_level=1),
                            "type": "802-3-ethernet",
                            "uuid": dbus.String("c3e38405-9c17-4155-ad70-664311b49066", variant_level=1),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    "ipv4": dbus.Dictionary(
                        {"method": dbus.String("auto", variant_level=1)}, signature=dbus.Signature("sv")
                    ),
                },
                signature=dbus.Signature("sa{sv}"),
            )
        )

    def add_wb_gsm_sim1(self):
        self.settings.AddConnection(
            dbus.Dictionary(
                {
                    "connection": dbus.Dictionary(
                        {
                            "autoconnect": dbus.Boolean(False, variant_level=1),
                            "id": dbus.String("wb-gsm-sim1", variant_level=1),
                            "type": "gsm",
                            "uuid": dbus.String("5d4297ba-c319-4c05-a153-17cb42e6e196", variant_level=1),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    "gsm": dbus.Dictionary(
                        {
                            "auto-config": dbus.Boolean(True, variant_level=1),
                            "sim-slot": dbus.Int32(1, variant_level=1),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    "ipv4": dbus.Dictionary(
                        {"method": dbus.String("auto", variant_level=1)}, signature=dbus.Signature("sv")
                    ),
                },
                signature=dbus.Signature("sa{sv}"),
            )
        )

    def add_wb_gsm_sim2(self):
        self.settings.AddConnection(
            dbus.Dictionary(
                {
                    "connection": dbus.Dictionary(
                        {
                            "autoconnect": dbus.Boolean(False, variant_level=1),
                            "id": dbus.String("wb-gsm-sim2", variant_level=1),
                            "type": "gsm",
                            "uuid": dbus.String("8b9964d4-b8dd-34d3-a3ed-481840bcf8c9", variant_level=1),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    "gsm": dbus.Dictionary(
                        {
                            "auto-config": dbus.Boolean(True, variant_level=1),
                            "sim-slot": dbus.Int32(2, variant_level=1),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    "ipv4": dbus.Dictionary(
                        {"method": dbus.String("auto", variant_level=1)}, signature=dbus.Signature("sv")
                    ),
                },
                signature=dbus.Signature("sa{sv}"),
            )
        )

    def add_wb_ap(self):
        self.settings.AddConnection(
            dbus.Dictionary(
                {
                    "connection": dbus.Dictionary(
                        {
                            "id": dbus.String("wb-ap", variant_level=1),
                            "interface-name": dbus.String("wlan0", variant_level=1),
                            "type": "802-11-wireless",
                            "uuid": dbus.String("d12c8d3c-1abe-4832-9b71-4ed6e3c20885", variant_level=1),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    "802-11-wireless": dbus.Dictionary(
                        {
                            "mode": dbus.String("ap", variant_level=1),
                            "ssid": dbus.String("WirenBoard-XXXXXXXX", variant_level=1),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    "ipv4": dbus.Dictionary(
                        {
                            "address-data": dbus.Array(
                                [
                                    dbus.Dictionary(
                                        {
                                            "address": dbus.String("192.168.42.1", variant_level=1),
                                            "prefix": dbus.Int32(24, variant_level=1),
                                        },
                                        signature=dbus.Signature("sv"),
                                    )
                                ],
                                signature=dbus.Signature("a{sv}"),
                            ),
                            "method": dbus.String("shared", variant_level=1),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                },
                signature=dbus.Signature("sa{sv}"),
            )
        )

    def test_to_json(self):
        # pylint: disable=R0915

        self.networkmanager_mock.AddEthernetDevice("mock_eth0", "eth0", DeviceState.ACTIVATED)
        self.networkmanager_mock.AddEthernetDevice("mock_eth1", "eth1", DeviceState.ACTIVATED)
        self.networkmanager_mock.AddWiFiDevice("mock_wlan0", "wlan0", DeviceState.ACTIVATED)
        self.networkmanager_mock.AddWiFiDevice("mock_wlan1", "wlan1", DeviceState.ACTIVATED)

        self.add_wb_eth0()
        self.add_wb_eth1()
        self.add_wb_gsm_sim1()
        self.add_wb_gsm_sim2()
        self.add_wb_ap()

        res = to_json(
            args=argparse.Namespace(
                config="tests/data/wb-connection-manager.conf",
                interfaces_conf="tests/data/interfaces",
                no_scan=True,
            )
        )

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
        assert len(res["ui"]["connections"]) == 9
        assert res["ui"]["connections"][0]["802-11-wireless-security"]["security"] == "none"
        assert res["ui"]["connections"][0]["802-11-wireless_mode"] == "ap"
        assert res["ui"]["connections"][0]["802-11-wireless_ssid"] == "WirenBoard-XXXXXXXX"
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
        assert res["ui"]["connections"][3]["gsm_auto-config"] is True
        assert res["ui"]["connections"][3]["gsm_sim-slot"] == 1
        assert res["ui"]["connections"][3]["ipv4"]["method"] == "auto"
        assert res["ui"]["connections"][3]["type"] == "02_nm_modem"
        assert res["ui"]["connections"][4]["connection_autoconnect"] is False
        assert res["ui"]["connections"][4]["connection_id"] == "wb-gsm-sim2"
        assert res["ui"]["connections"][4]["connection_uuid"] == "8b9964d4-b8dd-34d3-a3ed-481840bcf8c9"
        assert res["ui"]["connections"][4]["gsm_auto-config"] is True
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
        assert res["ui"]["connections"][6]["options"]["pre-up"] == "wb-set-mac"
        assert res["ui"]["connections"][6]["type"] == "dhcp"
        assert res["ui"]["connections"][7]["allow-hotplug"] is True
        assert res["ui"]["connections"][7]["auto"] is False
        assert res["ui"]["connections"][7]["method"] == "dhcp"
        assert res["ui"]["connections"][7]["mode"] == "inet"
        assert res["ui"]["connections"][7]["name"] == "eth1"
        assert res["ui"]["connections"][7]["options"]["hostname"] == "WirenBoard"
        assert res["ui"]["connections"][7]["options"]["pre-up"] == "wb-set-mac"
        assert res["ui"]["connections"][7]["type"] == "dhcp"
        assert res["ui"]["connections"][8]["allow-hotplug"] is True
        assert res["ui"]["connections"][8]["auto"] is False
        assert res["ui"]["connections"][8]["method"] == "static"
        assert res["ui"]["connections"][8]["mode"] == "can"
        assert res["ui"]["connections"][8]["name"] == "can0"
        assert res["ui"]["connections"][8]["options"]["bitrate"] == 125000
        assert res["ui"]["connections"][8]["type"] == "can"


def test_from_json():
    with open("tests/data/ui.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    res = from_json(
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
