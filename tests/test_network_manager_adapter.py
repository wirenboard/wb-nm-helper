from unittest.mock import MagicMock, patch

import dbus
import pytest

from wb.nm_helper.network_manager_adapter import (
    DBUSSettings,
    JSONSettings,
    ModemConnection,
    NetworkManagerAdapter,
    WiFiAp,
    has_rtl8723bu,
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
                "802-11-wireless_hidden": False,
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
                            dbus.String("hidden"): dbus.Boolean(False, variant_level=1),
                            dbus.String("powersave"): dbus.Int32(2, variant_level=1),
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
                            dbus.String("hidden"): dbus.Boolean(False, variant_level=1),
                            dbus.String("powersave"): dbus.Int32(2, variant_level=1),
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
                "802-11-wireless_hidden": False,
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
                            dbus.String("hidden"): dbus.Boolean(False, variant_level=1),
                            dbus.String("powersave"): dbus.Int32(2, variant_level=1),
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
                            dbus.String("hidden"): dbus.Boolean(False, variant_level=1),
                            dbus.String("powersave"): dbus.Int32(2, variant_level=1),
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
    access_point = WiFiAp()
    json_settings = JSONSettings(json)
    dbus_old_settings = DBUSSettings(dbus_old)
    dbus_new_settings = DBUSSettings(dbus_new)
    res = access_point.set_dbus_options(dbus_old_settings, json_settings)
    assert getattr(res, "clear_secrets") is False
    assert dbus_old_settings.params == dbus_new_settings.params


def make_network_manager(drivers):
    network_manager = MagicMock()
    devices = []
    for driver in drivers:
        device = MagicMock()
        device.get_property.return_value = driver
        devices.append(device)
    network_manager.get_devices.return_value = devices
    return network_manager


@pytest.mark.parametrize(
    "drivers,expected",
    [
        (["rtl8723bu", "rtl8723bu"], True),
        (["rtl8733bu", "rtl8733bu"], False),
        (["rtl8733bu", "rtl8723bu"], True),
        ([], False),
    ],
)
def test_has_rtl8723bu(drivers, expected):
    assert has_rtl8723bu(make_network_manager(drivers)) is expected


@pytest.mark.parametrize(
    "drivers,expected_bands",
    [
        # rtl8723bu claims a 5GHz support it does not have
        (["rtl8723bu", "rtl8723bu"], ["bg"]),
        (["rtl8733bu", "rtl8733bu"], ["bg", "a"]),
    ],
)
def test_get_wifi_bands(drivers, expected_bands):
    with patch(
        "wb.nm_helper.network_manager_adapter.NetworkManager",
        return_value=make_network_manager(drivers),
    ):
        assert NetworkManagerAdapter().get_wifi_bands() == expected_bands


@pytest.mark.parametrize(
    "drivers,expected_pmf",
    [
        # rtl8723bu has no 802.11w support at all, PMF must be turned off explicitly
        (["rtl8723bu", "rtl8723bu"], 1),
        # rtl8733bu can do 802.11w, so PMF is left at NetworkManager's own default
        (["rtl8733bu", "rtl8733bu"], None),
    ],
)
def test_wifiap_set_dbus_options_pmf(drivers, expected_pmf):
    json_settings = JSONSettings(
        {
            "802-11-wireless-security": {
                "security": "wpa-psk",
                "key-mgmt": "wpa-psk",
                "psk": "0123456789",
                "encryption": "AES/CCMP",
            },
            "802-11-wireless_mode": "ap",
            "802-11-wireless_ssid": "WirenBoard-APT6KWYK",
            "802-11-wireless_hidden": False,
            "connection_interface-name": "wlan0",
            "ipv4": {"method": "shared"},
            "type": "04_nm_wifi_ap",
            "connection_autoconnect": False,
            "connection_id": "wb-ap",
            "connection_uuid": "d12c8d3c-1abe-4832-9b71-4ed6e3c20885",
        }
    )
    dbus_settings = DBUSSettings()
    with patch(
        "wb.nm_helper.network_manager_adapter.NetworkManager",
        return_value=make_network_manager(drivers),
    ):
        WiFiAp().set_dbus_options(dbus_settings, json_settings)
    # the security section must survive, otherwise nothing below is actually checked
    assert dbus_settings.get_opt("802-11-wireless-security.key-mgmt") == "wpa-psk"
    assert dbus_settings.get_opt("802-11-wireless-security.psk") == "0123456789"
    assert dbus_settings.get_opt("802-11-wireless-security.pmf") == expected_pmf
    # WPS is disabled regardless of the chip
    assert dbus_settings.get_opt("802-11-wireless-security.wps-method") == 1


@pytest.mark.parametrize(
    "json,dbus_old,dbus_new,clear_secrets",
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
                    dbus.String("ipv6"): dbus.Dictionary(
                        {
                            dbus.String("method"): "ignore",
                        },
                        signature=dbus.Signature("sv"),
                    ),
                },
                signature=dbus.Signature("sa{sv}"),
            ),
            False,
        ),
        # Reset password
        (
            {
                "connection_autoconnect": False,
                "connection_id": "wb-gsm-sim1",
                "connection_uuid": "5d4297ba-c319-4c05-a153-17cb42e6e196",
                "gsm_auto-config": False,
                "gsm_sim-slot": 1,
                "gsm_password": "",
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
                            dbus.String("password"): "password",
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
                            dbus.String("auto-config"): True,
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
                    dbus.String("ipv6"): dbus.Dictionary(
                        {
                            dbus.String("method"): "ignore",
                        },
                        signature=dbus.Signature("sv"),
                    ),
                },
                signature=dbus.Signature("sa{sv}"),
            ),
            True,
        ),
    ],
)
def test_modem_set_dbus_options(json, dbus_old, dbus_new, clear_secrets):
    access_point = ModemConnection()
    json_settings = JSONSettings(json)
    dbus_old_settings = DBUSSettings(dbus_old)
    dbus_new_settings = DBUSSettings(dbus_new)
    res = access_point.set_dbus_options(dbus_old_settings, json_settings)
    assert clear_secrets == getattr(res, "clear_secrets")
    assert dbus_old_settings.params == dbus_new_settings.params
