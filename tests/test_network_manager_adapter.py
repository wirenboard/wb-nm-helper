import dbus
import pytest

from wb.nm_helper.network_manager_adapter import (
    DBUSSettings,
    JSONSettings,
    ModemConnection,
    WiFiAp,
)


@pytest.mark.parametrize(
    "json,dbus_old,dbus_new",
    [
        # Remove WPA-PSK security
        (
            {
                "802-11-wireless-security": {"security": "none"},
                "802-11-wireless_mode": "ap",
                "802-11-wireless_ssid": "WirenBoard-APT6KWYK",
                "connection_interface-name": "wlan0",
                "ipv4": {"method": "shared"},
                "type": "04_nm_wifi_ap",
                "connection_autoconnect": False,
                "connection_id": "wb-ap",
                "connection_uuid": "d12c8d3c-1abe-4832-9b71-4ed6e3c20885",
            },
            dbus.Dictionary(
                {
                    dbus.String("connection"): dbus.Dictionary(
                        {
                            dbus.String("autoconnect"): dbus.Boolean(False, variant_level=1),
                            dbus.String("id"): dbus.String("wb-ap", variant_level=1),
                            dbus.String("interface-name"): dbus.String("wlan0", variant_level=1),
                            dbus.String("type"): dbus.String("802-11-wireless", variant_level=1),
                            dbus.String("uuid"): dbus.String(
                                "d12c8d3c-1abe-4832-9b71-4ed6e3c20885", variant_level=1
                            ),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    dbus.String("802-11-wireless"): dbus.Dictionary(
                        {
                            dbus.String("mode"): dbus.String("ap", variant_level=1),
                            dbus.String("security"): dbus.String("802-11-wireless-security", variant_level=1),
                            dbus.String("ssid"): dbus.ByteArray(b"WirenBoard-APT6KWYK"),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    dbus.String("802-11-wireless-security"): dbus.Dictionary(
                        {dbus.String("key-mgmt"): dbus.String("wpa-psk", variant_level=1)},
                        signature=dbus.Signature("sv"),
                    ),
                    dbus.String("ipv4"): dbus.Dictionary(
                        {
                            dbus.String("address-data"): dbus.Array(
                                [], signature=dbus.Signature("a{sv}"), variant_level=1
                            ),
                            dbus.String("addresses"): dbus.Array(
                                [], signature=dbus.Signature("au"), variant_level=1
                            ),
                            dbus.String("method"): dbus.String("shared", variant_level=1),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                },
                signature=dbus.Signature("sa{sv}"),
            ),
            dbus.Dictionary(
                {
                    dbus.String("connection"): dbus.Dictionary(
                        {
                            dbus.String("autoconnect"): False,
                            dbus.String("id"): "wb-ap",
                            dbus.String("interface-name"): "wlan0",
                            dbus.String("type"): dbus.String("802-11-wireless", variant_level=1),
                            dbus.String("uuid"): "d12c8d3c-1abe-4832-9b71-4ed6e3c20885",
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    dbus.String("802-11-wireless"): dbus.Dictionary(
                        {
                            dbus.String("mode"): "ap",
                            dbus.String("ssid"): dbus.ByteArray(b"WirenBoard-APT6KWYK"),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    dbus.String("ipv4"): dbus.Dictionary(
                        {
                            dbus.String("method"): "shared",
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    dbus.String("user"): dbus.Dictionary(
                        {
                            dbus.String("data"): dbus.Dictionary(
                                {"wb.disable-nat": "false"}, signature=dbus.Signature("ss")
                            )
                        },
                        signature=dbus.Signature("sv"),
                    ),
                },
                signature=dbus.Signature("sa{sv}"),
            ),
        ),
        # Set Wi-Fi AP subnet address
        (
            {
                "802-11-wireless-security": {"security": "none"},
                "802-11-wireless_mode": "ap",
                "802-11-wireless_ssid": "WirenBoard-APT6KWYK",
                "connection_interface-name": "wlan0",
                "ipv4": {"method": "shared", "address": "192.168.42.1"},
                "type": "04_nm_wifi_ap",
                "connection_autoconnect": False,
                "connection_id": "wb-ap",
                "connection_uuid": "d12c8d3c-1abe-4832-9b71-4ed6e3c20885",
            },
            dbus.Dictionary(
                {
                    dbus.String("connection"): dbus.Dictionary(
                        {
                            dbus.String("autoconnect"): dbus.Boolean(False, variant_level=1),
                            dbus.String("id"): dbus.String("wb-ap", variant_level=1),
                            dbus.String("interface-name"): dbus.String("wlan0", variant_level=1),
                            dbus.String("type"): dbus.String("802-11-wireless", variant_level=1),
                            dbus.String("uuid"): dbus.String(
                                "d12c8d3c-1abe-4832-9b71-4ed6e3c20885", variant_level=1
                            ),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    dbus.String("802-11-wireless"): dbus.Dictionary(
                        {
                            dbus.String("mode"): dbus.String("ap", variant_level=1),
                            dbus.String("ssid"): dbus.ByteArray(b"WirenBoard-APT6KWYK"),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    dbus.String("ipv4"): dbus.Dictionary(
                        {
                            dbus.String("address-data"): dbus.Array(
                                [], signature=dbus.Signature("a{sv}"), variant_level=1
                            ),
                            dbus.String("addresses"): dbus.Array(
                                [], signature=dbus.Signature("au"), variant_level=1
                            ),
                            dbus.String("method"): dbus.String("shared", variant_level=1),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                },
                signature=dbus.Signature("sa{sv}"),
            ),
            dbus.Dictionary(
                {
                    dbus.String("connection"): dbus.Dictionary(
                        {
                            dbus.String("autoconnect"): False,
                            dbus.String("id"): "wb-ap",
                            dbus.String("interface-name"): "wlan0",
                            dbus.String("type"): dbus.String("802-11-wireless", variant_level=1),
                            dbus.String("uuid"): "d12c8d3c-1abe-4832-9b71-4ed6e3c20885",
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    dbus.String("802-11-wireless"): dbus.Dictionary(
                        {
                            dbus.String("mode"): "ap",
                            dbus.String("ssid"): dbus.ByteArray(b"WirenBoard-APT6KWYK"),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    dbus.String("ipv4"): dbus.Dictionary(
                        {
                            dbus.String("method"): "shared",
                            dbus.String("address-data"): dbus.Array(
                                [
                                    dbus.Dictionary(
                                        {"address": "192.168.42.1", "prefix": dbus.UInt32(24)}, signature=None
                                    )
                                ],
                                signature=dbus.Signature("a{sv}"),
                            ),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    dbus.String("user"): dbus.Dictionary(
                        {
                            dbus.String("data"): dbus.Dictionary(
                                {"wb.disable-nat": "false"}, signature=dbus.Signature("ss")
                            )
                        },
                        signature=dbus.Signature("sv"),
                    ),
                },
                signature=dbus.Signature("sa{sv}"),
            ),
        ),
    ],
)
def test_wifiap_set_dbus_options(json, dbus_old, dbus_new):
    ap = WiFiAp()
    json_settings = JSONSettings(json)
    dbus_old_settings = DBUSSettings(dbus_old)
    dbus_new_settings = DBUSSettings(dbus_new)
    ap.set_dbus_options(dbus_old_settings, json_settings)
    assert dbus_old_settings.params == dbus_new_settings.params


@pytest.mark.parametrize(
    "json,dbus_old,dbus_new",
    [
        # Set GSM APN
        (
            {
                "connection_autoconnect": False,
                "connection_id": "wb-gsm-sim1",
                "connection_uuid": "5d4297ba-c319-4c05-a153-17cb42e6e196",
                "gsm_apn": "internet",
                "gsm_auto-config": False,
                "gsm_sim-slot": 1,
                "ipv4": {"method": "auto"},
                "type": "02_nm_modem",
            },
            dbus.Dictionary(
                {
                    dbus.String("connection"): dbus.Dictionary(
                        {
                            dbus.String("autoconnect"): False,
                            dbus.String("id"): "wb-gsm-sim1",
                            dbus.String("type"): "gsm",
                            dbus.String("uuid"): "5d4297ba-c319-4c05-a153-17cb42e6e196",
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    dbus.String("gsm"): dbus.Dictionary(
                        {
                            dbus.String("auto-config"): dbus.Boolean(True, variant_level=1),
                            dbus.String("sim-slot"): dbus.Int32(1, variant_level=1),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    dbus.String("ipv4"): dbus.Dictionary(
                        {
                            dbus.String("method"): "auto",
                        },
                        signature=dbus.Signature("sv"),
                    ),
                },
                signature=dbus.Signature("sa{sv}"),
            ),
            dbus.Dictionary(
                {
                    dbus.String("connection"): dbus.Dictionary(
                        {
                            dbus.String("autoconnect"): False,
                            dbus.String("id"): "wb-gsm-sim1",
                            dbus.String("type"): "gsm",
                            dbus.String("uuid"): "5d4297ba-c319-4c05-a153-17cb42e6e196",
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    dbus.String("gsm"): dbus.Dictionary(
                        {
                            dbus.String("auto-config"): False,
                            dbus.String("apn"): "internet",
                            dbus.String("sim-slot"): dbus.Int32(1, variant_level=1),
                        },
                        signature=dbus.Signature("sv"),
                    ),
                    dbus.String("ipv4"): dbus.Dictionary(
                        {
                            dbus.String("method"): "auto",
                        },
                        signature=dbus.Signature("sv"),
                    ),
                },
                signature=dbus.Signature("sa{sv}"),
            ),
        ),
    ],
)
def test_modem_set_dbus_options(json, dbus_old, dbus_new):
    ap = ModemConnection()
    json_settings = JSONSettings(json)
    dbus_old_settings = DBUSSettings(dbus_old)
    dbus_new_settings = DBUSSettings(dbus_new)
    ap.set_dbus_options(dbus_old_settings, json_settings)
    assert dbus_old_settings.params == dbus_new_settings.params
