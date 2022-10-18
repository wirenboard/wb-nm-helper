from __future__ import annotations

import datetime
import logging
import time
from ipaddress import IPv4Interface

import dbus
from packaging import version

from .network_manager import (
    NM_DEVICE_TYPE_ETHERNET,
    NM_DEVICE_TYPE_MODEM,
    NM_DEVICE_TYPE_WIFI,
    NetworkManager,
    NMConnection,
    NMWirelessDevice,
)
from .network_managing_system import INetworkManagingSystem

WIFI_SCAN_TIMEOUT = datetime.timedelta(seconds=5)

METHOD_ETHERNET = "01_nm_ethernet"
METHOD_MODEM = "02_nm_modem"
METHOD_WIFI = "03_nm_wifi"


def to_mac_string(mac_array):
    if mac_array is None:
        return None
    return ":".join(map(lambda item: "%0.2X" % item, mac_array))


def to_ascii_string(data):
    if data is None:
        return None
    return "".join(map(lambda item: "%c" % item, data))


def to_mac_list(mac_string):
    if mac_string is None:
        return None
    return dbus.Array(map(lambda item: dbus.Byte(int(item, 16)), mac_string.split(":")))


def not_empty_string(value: str):
    if len(value) == 0:
        return None
    return value


def to_dbus_byte_array(val):
    return dbus.ByteArray(val.encode("utf-8"))


def minus_one_is_none(val):
    if val == -1:
        return None
    return val


def get_opt(opt_dict, path, default=None):
    if path is None:
        return opt_dict
    path_items = path.split(".")
    obj = opt_dict
    for i in range(len(path_items) - 1):
        obj = obj.get(path_items[i])
        if obj is None:
            return default
    return obj.get(path_items[len(path_items) - 1], default)


def set_opt(dst, dst_path, src, src_path=None, convert_fn=None):
    value = get_opt(src, src_path)
    if convert_fn is not None:
        value = convert_fn(value)
    dst_path_items = dst_path.split(".")
    last_dst_item = dst_path_items[len(dst_path_items) - 1]
    if isinstance(dst, dbus.Dictionary):
        obj = dst
        for i in range(len(dst_path_items) - 1):
            obj = obj.setdefault(dst_path_items[i], dbus.Dictionary(signature="sv"))
        if value is None:
            if last_dst_item in obj:
                del obj[last_dst_item]
        else:
            obj[last_dst_item] = value
    else:
        if value is not None:
            obj = dst
            for i in range(len(dst_path_items) - 1):
                obj = obj.setdefault(dst_path_items[i], {})
            obj[last_dst_item] = value


def remove_undefined_connections(interfaces, network_manager: NetworkManager):
    uids = [get_opt(i, "uuid") for i in interfaces if get_opt(i, "uuid") is not None]
    handlers = [EthernetConnection(), WiFiConnection(), ModemConnection()]
    for con in network_manager.get_connections():
        c_settings = con.get_settings()
        for handler in handlers:
            if (c_settings["connection"]["uuid"] not in uids) and handler.can_manage(c_settings):
                con.delete()
                break


def set_ipv4_dbus_options(con, iface):
    # remove deprecated parameter ipv4.addresses
    set_opt(con, "ipv4.addresses", None)
    if get_opt(iface, "ipv4.method", "auto") == "manual":
        set_opt(con, "ipv4.method", "manual")
        set_opt(con, "ipv4.gateway", iface, "ipv4.gateway")
        set_opt(con, "ipv4.route-metric", iface, "ipv4.metric")
        net = IPv4Interface(
            "%s/%s" % (get_opt(iface, "ipv4.address"), get_opt(iface, "ipv4.netmask", "255.255.255.0"))
        )
        parts = net.with_prefixlen.split("/")
        addr = dbus.Dictionary({"address": parts[0], "prefix": dbus.UInt32(parts[1])})
        con["ipv4"]["address-data"] = dbus.Array([addr], signature=dbus.Signature("a{sv}"))
        set_opt(con, "ipv4.dhcp-hostname", None)
        set_opt(con, "ipv4.dhcp-client-id", None)
    else:
        set_opt(con, "ipv4.method", "auto")
        set_opt(con, "ipv4.dhcp-hostname", iface, "ipv4.hostname", not_empty_string)
        set_opt(con, "ipv4.dhcp-client-id", iface, "ipv4.client", not_empty_string)
        set_opt(con, "ipv4.gateway", None)
        set_opt(con, "ipv4.address-data", None)
        set_opt(con, "ipv4.route-metric", None)


def get_ipv4_dbus_options(res, cfg):
    set_opt(res, "ipv4.hostname", cfg, "ipv4.dhcp-hostname")
    set_opt(res, "ipv4.client", cfg, "ipv4.dhcp-client-id")
    set_opt(res, "ipv4.gateway", cfg, "ipv4.gateway")
    set_opt(res, "ipv4.metric", cfg, "ipv4.route-metric")
    set_opt(res, "ipv4.method", cfg, "ipv4.method")
    addr_data = get_opt(cfg, "ipv4.address-data")
    if addr_data is not None and len(addr_data) > 0:
        net = IPv4Interface("%s/%d" % (addr_data[0].get("address"), addr_data[0].get("prefix")))
        parts = net.with_netmask.split("/")
        set_opt(res, "ipv4.address", parts[0])
        set_opt(res, "ipv4.netmask", parts[1])


def set_common_dbus_options(con, iface):
    if iface.get("uuid"):
        set_opt(con, "connection.uuid", iface, "uuid")
    set_opt(con, "connection.id", iface, "name")
    set_opt(con, "connection.interface-name", iface, "device", not_empty_string)
    set_opt(con, "connection.autoconnect", iface.get("auto", False))


def get_common_dbus_options(cfg, method):
    res = {
        "method": method,
        "name": cfg["connection"]["id"],
        "auto": bool(cfg["connection"].get("autoconnect", True)),
        "uuid": cfg["connection"]["uuid"],
    }
    set_opt(res, "device", cfg, "connection.interface-name")
    return res


class EthernetConnection:
    def set_dbus_options(self, con, iface, _):
        set_common_dbus_options(con, iface)
        set_opt(con, "802-3-ethernet.cloned-mac-address", iface, "hwaddress", to_mac_list)
        set_opt(con, "802-3-ethernet.mtu", iface, "mtu")
        set_ipv4_dbus_options(con, iface)

    def create(self, iface, nm_version) -> dbus.Dictionary:
        con = dbus.Dictionary(signature="sv")
        self.set_dbus_options(con, iface, nm_version)
        set_opt(con, "connection.id", iface, "name")
        return con

    def can_manage(self, cfg):
        return cfg["connection"]["type"] in ["802-3-ethernet"]

    def read(self, con: NMConnection):
        cfg = con.get_settings()
        if not self.can_manage(cfg):
            return None
        res = get_common_dbus_options(cfg, METHOD_ETHERNET)
        set_opt(res, "hwaddress", cfg, "802-3-ethernet.cloned-mac-address", to_mac_string)
        set_opt(res, "mtu", cfg, "802-3-ethernet.mtu")
        get_ipv4_dbus_options(res, cfg)
        return res


class WiFiConnection:
    def set_dbus_options(self, con, iface, _):
        set_common_dbus_options(con, iface)
        set_opt(con, "802-11-wireless.cloned-mac-address", iface, "hwaddress", to_mac_list)
        set_opt(con, "802-11-wireless.ssid", iface, "ssid", to_dbus_byte_array)
        wpa_psk = get_opt(iface, "wpa-psk")
        if wpa_psk is not None:
            set_opt(con, "802-11-wireless-security.key-mgmt", "wpa-psk")
            set_opt(con, "802-11-wireless-security.psk", wpa_psk)
        set_ipv4_dbus_options(con, iface)

    def create(self, iface, nm_version) -> dbus.Dictionary:
        con = dbus.Dictionary(signature="sv")
        self.set_dbus_options(con, iface, nm_version)
        set_opt(con, "connection.id", iface["name"])
        return con

    def can_manage(self, cfg):
        return cfg["connection"]["type"] in ["802-11-wireless"]

    def read(self, con: NMConnection):
        cfg = con.get_settings()
        if not self.can_manage(cfg):
            return None
        res = get_common_dbus_options(cfg, METHOD_WIFI)
        set_opt(res, "hwaddress", cfg, "802-11-wireless.cloned-mac-address", to_mac_string)
        set_opt(res, "ssid", cfg, "802-11-wireless.ssid", to_ascii_string)
        set_opt(res, "mtu", cfg, "802-11-wireless.mtu")
        if get_opt(cfg, "802-11-wireless-security.key-mgmt") == "wpa-psk":
            set_opt(
                res,
                "wpa-psk",
                get_opt(
                    con.get_iface().GetSecrets("802-11-wireless-security"), "802-11-wireless-security.psk"
                ),
            )
        get_ipv4_dbus_options(res, cfg)
        return res


class ModemConnection:
    def set_dbus_options(self, con, iface, nm_version):
        set_common_dbus_options(con, iface)
        set_opt(con, "gsm.sim-slot", iface, "sim-slot")
        if len(get_opt(iface, "apn", "")) != 0:
            set_opt(con, "gsm.apn", iface, "apn")
            set_opt(con, "gsm.auto-config", None)
        else:
            # gsm.auto-config was implemented in NM 1.22.0
            if version.parse(nm_version) >= version.parse("1.22.0"):
                set_opt(con, "gsm.auto-config", True)

    def create(self, iface, nm_version) -> dbus.Dictionary:
        con = dbus.Dictionary(signature="sv")
        self.set_dbus_options(con, iface, nm_version)
        set_opt(con, "connection.id", iface["name"])
        set_opt(con, "connection.type", "gsm")
        return con

    def can_manage(self, cfg):
        return cfg["connection"]["type"] in ["gsm"]

    def read(self, con: NMConnection):
        cfg = con.get_settings()
        if not self.can_manage(cfg):
            return None
        res = get_common_dbus_options(cfg, METHOD_MODEM)
        set_opt(res, "apn", cfg, "gsm.apn")
        set_opt(res, "sim-slot", cfg, "gsm.sim-slot", minus_one_is_none)
        get_ipv4_dbus_options(res, cfg)
        return res


def apply(iface, c_handler, network_manager: NetworkManager):
    if iface.get("uuid"):
        for con in network_manager.get_connections():
            c_settings = con.get_settings()
            if c_settings["connection"]["uuid"] == iface["uuid"]:
                if c_settings["connection"]["id"] == iface["name"]:
                    c_handler.set_dbus_options(c_settings, iface, network_manager.get_version())
                    con.update_settings(c_settings)
                else:
                    con.delete()
                    network_manager.add_connection(c_handler.create(iface, network_manager.get_version()))
                return
    network_manager.add_connection(c_handler.create(iface, network_manager.get_version()))


class NetworkManagerAdapter(INetworkManagingSystem):
    @staticmethod
    def probe():
        try:
            network_manager = NetworkManager()
            return NetworkManagerAdapter(network_manager.get_version())
        except dbus.exceptions.DBusException:
            return None

    def __init__(self, nm_version):
        self.nm_version = nm_version

    def apply(self, interfaces):
        network_manager = NetworkManager()
        remove_undefined_connections(interfaces, network_manager)
        unmanaged_interfaces = []
        handlers = {
            METHOD_ETHERNET: EthernetConnection(),
            METHOD_MODEM: ModemConnection(),
            METHOD_WIFI: WiFiConnection(),
        }
        for iface in interfaces:
            handler = handlers.get(iface["method"])
            if handler is not None:
                apply(iface, handler, network_manager)
            else:
                unmanaged_interfaces.append(iface)
        return unmanaged_interfaces

    def read(self):
        handlers = [EthernetConnection(), WiFiConnection(), ModemConnection()]
        res = []
        network_manager = NetworkManager()
        for con in network_manager.get_connections():
            for handler in handlers:
                cfg = handler.read(con)
                if cfg is not None:
                    res.append(cfg)
                    break
        return res

    def scan_if_needed(self, dev: NMWirelessDevice) -> None:
        last_scan_ms = dev.get_property("LastScan")
        # nmcli requests scan if last one was more than 30 seconds ago
        if (last_scan_ms != -1) and (
            time.clock_gettime_ns(time.CLOCK_BOOTTIME) / 1000000 - last_scan_ms < 30000
        ):
            return
        dev.request_wifi_scan()
        # Documentation says:
        #   To know when the scan is finished, use the "PropertiesChanged" signal
        #   from "org.freedesktop.DBus.Properties" to listen to changes to the "LastScan" property.
        #
        # Simply poll "LastScan"
        start = datetime.datetime.now()
        while start - datetime.datetime.now() <= WIFI_SCAN_TIMEOUT:
            if last_scan_ms != dev.get_property("LastScan"):
                return
            time.sleep(1)

    def get_wifi_ssids(self) -> list[str]:
        network_manager = NetworkManager()
        dev = network_manager.find_device_by_param("DeviceType", NM_DEVICE_TYPE_WIFI)
        if not dev:
            return []
        try:
            wireless_dev = NMWirelessDevice(dev)
            self.scan_if_needed(wireless_dev)
            res = []
            for access_point in wireless_dev.get_access_points():
                res.append(to_ascii_string(access_point.get_property("Ssid")))
            res.sort()
            return res
        except dbus.exceptions.DBusException as ex:
            logging.info("Error during Wi-Fi scan: %s", ex)

        return []

    def add_devices(self, devices: list[str]) -> list[str]:
        type_mapping = {
            NM_DEVICE_TYPE_ETHERNET: "ethernet",
            NM_DEVICE_TYPE_WIFI: "wifi",
            NM_DEVICE_TYPE_MODEM: "modem",
        }
        network_manager = NetworkManager()
        for dev in network_manager.get_devices():
            mapping = type_mapping.get(dev.get_property("DeviceType"))
            if mapping:
                devices.setdefault(mapping, []).append(dev.get_property("Interface"))
        return devices
