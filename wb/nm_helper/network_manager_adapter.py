from ipaddress import IPv4Interface
from packaging import version

import time
import datetime
import dbus
import logging

from .network_managing_system import NetworkManagingSystem

NM_IFACE_NAME = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
SETTINGS_IFACE_NAME = NM_IFACE_NAME + ".Settings"
SETTINGS_LIST_PATH = NM_PATH + "/Settings"
PROPS_IFACE_NAME = "org.freedesktop.DBus.Properties"

# from enum NMDeviceType
NM_DEVICE_TYPE_ETHERNET = 1
NM_DEVICE_TYPE_WIFI = 2
NM_DEVICE_TYPE_MODEM = 8

WIFI_SCAN_TIMEOUT = datetime.timedelta(seconds=5)

METHOD_ETHERNET = "01_nm_ethernet"
METHOD_MODEM = "02_nm_modem"
METHOD_WIFI = "03_nm_wifi"

DEV_TYPES = {
    1: "Ethernet",
    2: "Wi-Fi",
    5: "Bluetooth",
    6: "OLPC",
    7: "WiMAX",
    8: "Modem",
    9: "InfiniBand",
    10: "Bond",
    11: "VLAN",
    12: "ADSL",
    13: "Bridge",
    14: "Generic",
    15: "Team",
    16: "TUN",
    17: "IPTunnel",
    18: "MACVLAN",
    19: "VXLAN",
    20: "Veth",
}


def to_mac_string(mac_array):
    if mac_array is None:
        return None
    return ":".join(map(lambda item: "%0.2X" % item, mac_array))


def to_ascii_string(ar):
    if ar is None:
        return None
    return "".join(map(lambda item: "%c" % item, ar))


def to_mac_list(mac_string):
    if mac_string is None:
        return None
    return dbus.Array(map(lambda item: dbus.Byte(int(item, 16)), mac_string.split(":")))


def not_empty_string(str):
    if len(str) == 0:
        return None
    return str


def to_dbus_byte_array(val):
    return dbus.ByteArray(val.encode('utf-8'))


def minus_one_is_none(val):
    if val == -1:
        return None
    return val


def get_opt(opt_dict, path, default = None):
    if path is None:
        return opt_dict
    path_items = path.split(".")
    obj = opt_dict
    for i in range(len(path_items) - 1):
        obj = obj.get(path_items[i])
        if obj is None:
            return default
    return obj.get(path_items[len(path_items) - 1], default)


def set(dst, dst_path, src, src_path = None, convert_fn = None):
    value = get_opt(src, src_path)
    if convert_fn is not None:
        value = convert_fn(value)
    dst_path_items = dst_path.split(".")
    last_dst_item = dst_path_items[len(dst_path_items) - 1]
    if type(dst) is dbus.Dictionary:
        obj = dst
        for i in range(len(dst_path_items) - 1):
            obj = obj.setdefault(dst_path_items[i], dbus.Dictionary())
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


def remove_undefined_connections(interfaces, bus, settings):
    uids = [get_opt(i, "uuid") for i in interfaces if get_opt(i, "uuid") is not None]
    handlers = [ EthernetConnection(), WiFiConnection(), ModemConnection() ]
    for c_path in settings.ListConnections():
        c_proxy = bus.get_object(NM_IFACE_NAME, c_path)
        c_obj = dbus.Interface(c_proxy, SETTINGS_IFACE_NAME + ".Connection")
        c_settings = c_obj.GetSettings()
        for handler in handlers:
            if (c_settings["connection"]["uuid"] not in uids) and handler.can_manage(c_settings):
                c_obj.Delete()
                break


def set_ipv4_dbus_options(con, iface):
    # remove deprecated parameter ipv4.addresses
    set(con, "ipv4.addresses", None)
    if get_opt(iface, "ipv4.method", "auto") == "manual":
        set(con, "ipv4.method", "manual")
        set(con, "ipv4.gateway", iface, "ipv4.gateway")
        set(con, "ipv4.route-metric", iface, "ipv4.metric")
        net = IPv4Interface("%s/%s" % (get_opt(iface, "ipv4.address"), get_opt(iface, "ipv4.netmask", "255.255.255.0")))
        parts = net.with_prefixlen.split("/")
        addr = dbus.Dictionary({
            "address": parts[0],
            "prefix": dbus.UInt32(parts[1])
        })
        con["ipv4"]["address-data"] = dbus.Array([addr], signature=dbus.Signature("a{sv}"))
        set(con, "ipv4.dhcp-hostname",  None)
        set(con, "ipv4.dhcp-client-id", None)
    else:
        set(con, "ipv4.method", "auto")
        set(con, "ipv4.dhcp-hostname",  iface, "ipv4.hostname", not_empty_string)
        set(con, "ipv4.dhcp-client-id", iface, "ipv4.client", not_empty_string)
        set(con, "ipv4.gateway", None)
        set(con, "ipv4.address-data", None)
        set(con, "ipv4.route-metric", None)


def get_ipv4_dbus_options(res, cfg):
    set(res, "ipv4.hostname", cfg, "ipv4.dhcp-hostname")
    set(res, "ipv4.client", cfg, "ipv4.dhcp-client-id")
    set(res, "ipv4.gateway", cfg, "ipv4.gateway")
    set(res, "ipv4.metric", cfg, "ipv4.route-metric")
    set(res, "ipv4.method", cfg, "ipv4.method")
    addr_data = get_opt(cfg, "ipv4.address-data")
    if addr_data is not None and len(addr_data) > 0:
        net = IPv4Interface("%s/%d" % (addr_data[0].get("address"), addr_data[0].get("prefix")))
        parts = net.with_netmask.split("/")
        set(res, "ipv4.address", parts[0])
        set(res, "ipv4.netmask", parts[1])


def set_common_dbus_options(con, iface):
    if iface.get("uuid"):
        set(con, "connection.uuid", iface, "uuid")
    set(con, "connection.id", iface, "name")
    set(con, "connection.interface-name", iface, "device", not_empty_string)
    set(con, "connection.autoconnect", iface.get("auto", False))


def get_common_dbus_options(cfg, method):
    res = {
        "method": method,
        "name": cfg["connection"]["id"],
        "auto": bool(cfg["connection"].get("autoconnect", True)),
        "uuid": cfg["connection"]["uuid"]
    }
    set(res, "device", cfg, "connection.interface-name")
    return res


class EthernetConnection:
    def set_dbus_options(self, con, iface, nm_version):
        set_common_dbus_options(con, iface)
        set(con, "802-3-ethernet.cloned-mac-address", iface, "hwaddress", to_mac_list)
        set(con, "802-3-ethernet.mtu", iface, "mtu")
        set_ipv4_dbus_options(con, iface)


    def create(self, iface, settings_iface, nm_version):
        con = dbus.Dictionary()
        self.set_dbus_options(con, iface, nm_version)
        set(con, "connection.id", iface, "name")
        settings_iface.AddConnection(con)


    def can_manage(self, cfg):
        return (cfg["connection"]["type"] in ["802-3-ethernet"])


    def read(self, c_obj):
        cfg = c_obj.GetSettings()
        if not self.can_manage(cfg):
            return None
        res = get_common_dbus_options(cfg, METHOD_ETHERNET)
        set(res, "hwaddress", cfg, "802-3-ethernet.cloned-mac-address", to_mac_string)
        set(res, "mtu", cfg, "802-3-ethernet.mtu")
        get_ipv4_dbus_options(res, cfg)
        return res


class WiFiConnection:
    def set_dbus_options(self, con, iface, nm_version):
        set_common_dbus_options(con, iface)
        set(con, "802-11-wireless.cloned-mac-address", iface, "hwaddress", to_mac_list)
        set(con, "802-11-wireless.ssid", iface, "ssid", to_dbus_byte_array)
        wpa_psk = get_opt(iface, "wpa-psk")
        if wpa_psk is not None:
            set(con, "802-11-wireless-security.key-mgmt", "wpa-psk")
            set(con, "802-11-wireless-security.psk", wpa_psk)
        set_ipv4_dbus_options(con, iface)


    def create(self, iface, settings_iface, nm_version):
        con = dbus.Dictionary()
        self.set_dbus_options(con, iface, nm_version)
        set(con, "connection.id", iface["name"])
        settings_iface.AddConnection(con)


    def can_manage(self, cfg):
        return (cfg["connection"]["type"] in ["802-11-wireless"])


    def read(self, c_obj):
        cfg = c_obj.GetSettings()
        if not self.can_manage(cfg):
            return None
        res = get_common_dbus_options(cfg, METHOD_WIFI)
        set(res, "hwaddress", cfg, "802-11-wireless.cloned-mac-address", to_mac_string)
        set(res, "ssid",  cfg, "802-11-wireless.ssid", to_ascii_string)
        set(res, "mtu", cfg, "802-11-wireless.mtu")
        if get_opt(cfg, "802-11-wireless-security.key-mgmt") == "wpa-psk":
            set(res, "wpa-psk", get_opt(c_obj.GetSecrets("802-11-wireless-security"), "802-11-wireless-security.psk"))
        get_ipv4_dbus_options(res, cfg)
        return res


class ModemConnection:
    def set_dbus_options(self, con, iface, nm_version):
        set_common_dbus_options(con, iface)
        set(con, "gsm.sim-slot", iface, "sim-slot")
        if len(get_opt(iface, "apn", "")) != 0:
            set(con, "gsm.apn", iface, "apn")
            set(con, "gsm.auto-config", None)
        else:
            # gsm.auto-config was implemented in NM 1.22.0
            if version.parse(nm_version) >= version.parse("1.22.0"): 
                set(con, "gsm.auto-config", True)


    def create(self, iface, settings_iface, nm_version):
        con = dbus.Dictionary()
        self.set_dbus_options(con, iface, nm_version)
        set(con, "connection.id", iface["name"])
        set(con, "connection.type", "gsm")
        settings_iface.AddConnection(con)


    def can_manage(self, cfg):
        return (cfg["connection"]["type"] in ["gsm"])


    def read(self, c_obj):
        cfg = c_obj.GetSettings()
        if not self.can_manage(cfg):
            return None
        res = get_common_dbus_options(cfg, METHOD_MODEM)
        set(res, "apn", cfg, "gsm.apn")
        set(res, "sim-slot", cfg, "gsm.sim-slot", minus_one_is_none)
        get_ipv4_dbus_options(res, cfg)
        return res


def apply(iface, c_handler, bus, settings, nm_version):
    if iface.get("uuid"):
        for c_path in settings.ListConnections():
            c_proxy = bus.get_object(NM_IFACE_NAME, c_path)
            c_obj = dbus.Interface(c_proxy, SETTINGS_IFACE_NAME + ".Connection")
            c_settings = c_obj.GetSettings()
            if c_settings["connection"]["uuid"] == iface["uuid"]:
                if c_settings["connection"]["id"] == iface["name"]:
                    c_handler.set_dbus_options(c_settings, iface, nm_version)
                    c_obj.Update(c_settings)
                else:
                    c_obj.Delete()
                    c_handler.create(iface, settings, nm_version)
                return
    c_handler.create(iface, settings, nm_version)


class NetworkManagerAdapter(NetworkManagingSystem):

    @staticmethod
    def probe():
        try:
            bus = dbus.SystemBus() 
            obj = bus.get_object(NM_IFACE_NAME, NM_PATH)
            interface = dbus.Interface(obj, PROPS_IFACE_NAME)
            nm_version = interface.Get(NM_IFACE_NAME, "Version")
            return NetworkManagerAdapter(nm_version)
        except:
            return None


    def __init__(self, nm_version):
        self.nm_version = nm_version


    def apply(self, interfaces):
        bus = dbus.SystemBus()
        proxy = bus.get_object(NM_IFACE_NAME, SETTINGS_LIST_PATH)
        settings = dbus.Interface(proxy, SETTINGS_IFACE_NAME)
        remove_undefined_connections(interfaces, bus, settings)
        unmanaged_interfaces = []
        handlers = {
            METHOD_ETHERNET: EthernetConnection(),
            METHOD_MODEM: ModemConnection(),
            METHOD_WIFI: WiFiConnection()
        }
        for iface in interfaces:
            handler = handlers.get(iface["method"])
            if handler is not None:
                apply(iface, handler, bus, settings, self.nm_version)
            else:
                unmanaged_interfaces.append(iface)
        return unmanaged_interfaces


    def read(self):
        bus = dbus.SystemBus()
        proxy = bus.get_object(NM_IFACE_NAME, SETTINGS_LIST_PATH)
        settings = dbus.Interface(proxy, SETTINGS_IFACE_NAME)
        handlers = [ EthernetConnection(), WiFiConnection(), ModemConnection() ]
        res = []
        for path in settings.ListConnections():
            con_proxy = bus.get_object(NM_IFACE_NAME, path)
            c_obj = dbus.Interface(con_proxy, SETTINGS_IFACE_NAME + ".Connection")
            for handler in handlers:
                cfg = handler.read(c_obj)
                if cfg is not None:
                    res.append(cfg)
                    break
        return res

    def scan_if_needed(self, dev_proxy):
        prop_iface = dbus.Interface(dev_proxy, PROPS_IFACE_NAME)
        last_scan_ms = prop_iface.Get(NM_IFACE_NAME + ".Device.Wireless", "LastScan")
        # nmcli requests rescan if last one was more than 30 seconds ago
        if (last_scan_ms != -1) and (time.clock_gettime_ns(time.CLOCK_BOOTTIME) / 1000000 - last_scan_ms < 30000):
            return
        wireless_iface = dbus.Interface(dev_proxy, NM_IFACE_NAME + ".Device.Wireless")
        wireless_iface.RequestScan([])
        # Documentation says:
        #   To know when the scan is finished, use the "PropertiesChanged" signal 
        #   from "org.freedesktop.DBus.Properties" to listen to changes to the "LastScan" property.
        #
        # Simply poll "LastScan"
        start = datetime.datetime.now()
        while start - datetime.datetime.now() <= WIFI_SCAN_TIMEOUT:
            if  last_scan_ms != prop_iface.Get(NM_IFACE_NAME + ".Device.Wireless", "LastScan"):
                return
            time.sleep(1)

    def get_wifi_ssids(self):
        bus = dbus.SystemBus()
        nm_proxy = bus.get_object(NM_IFACE_NAME, NM_PATH)
        nm = dbus.Interface(nm_proxy, NM_IFACE_NAME)
        devices = nm.GetDevices()
        for d in devices:
            dev_proxy = bus.get_object(NM_IFACE_NAME, d)
            prop_iface = dbus.Interface(dev_proxy, PROPS_IFACE_NAME)
            if prop_iface.Get(NM_IFACE_NAME + ".Device", "DeviceType") == NM_DEVICE_TYPE_WIFI:
                try:
                    self.scan_if_needed(dev_proxy)
                    res = []
                    wireless_iface = dbus.Interface(dev_proxy, NM_IFACE_NAME + ".Device.Wireless")
                    for ap_path in wireless_iface.GetAllAccessPoints():
                        ap_proxy = bus.get_object(NM_IFACE_NAME, ap_path)
                        ap_iface = dbus.Interface(ap_proxy, PROPS_IFACE_NAME)
                        res.append(to_ascii_string(ap_iface.Get(NM_IFACE_NAME + ".AccessPoint", "Ssid")))
                    res.sort()
                    return res
                except dbus.exceptions.DBusException as ex:
                    logging.info('Error during Wi-Fi scan: %s', ex)

        return []

    def add_devices(self, devices):
        type_mapping = {
            NM_DEVICE_TYPE_ETHERNET: "ethernet",
            NM_DEVICE_TYPE_WIFI: "wifi",
            NM_DEVICE_TYPE_MODEM: "modem"
        }
        bus = dbus.SystemBus()
        nm_proxy = bus.get_object(NM_IFACE_NAME, NM_PATH)
        nm = dbus.Interface(nm_proxy, NM_IFACE_NAME)
        for d in nm.GetDevices():
            dev_proxy = bus.get_object(NM_IFACE_NAME, d)
            prop_iface = dbus.Interface(dev_proxy, PROPS_IFACE_NAME)
            mapping = type_mapping.get(prop_iface.Get(NM_IFACE_NAME + ".Device", "DeviceType"))
            if mapping:
                devices.setdefault(mapping, []).append(prop_iface.Get(NM_IFACE_NAME + ".Device", "Interface"))
        return devices
