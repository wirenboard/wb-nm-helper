from __future__ import annotations

import datetime
import json
import time
from collections import namedtuple
from enum import Enum
from ipaddress import IPv4Interface
from socket import htonl, ntohl
from typing import List, Optional, TypedDict

import dbus

from .network_management_system import (
    DEVICE_TYPE_ETHERNET,
    DEVICE_TYPE_MODEM,
    DEVICE_TYPE_WIFI,
)
from .network_manager import (
    NM_DEVICE_TYPE_ETHERNET,
    NM_DEVICE_TYPE_MODEM,
    NM_DEVICE_TYPE_WIFI,
    NM_WIFI_MODE_DEFAULT,
    NetworkManager,
    NMConnection,
    NMWirelessDevice,
)

METHOD_ETHERNET = "01_nm_ethernet"
METHOD_MODEM = "02_nm_modem"
METHOD_WIFI = "03_nm_wifi"
METHOD_WIFI_AP = "04_nm_wifi_ap"


class ParamPathType(Enum):
    FLAT = 1
    TREE = 2


class DeviceDesc(TypedDict):
    type: str
    iface: str


class SetDbusOptionsResult(TypedDict):
    clear_secrets: bool


Param = namedtuple(
    "Param", ["path", "to_dbus", "from_dbus", "json_path_type"], defaults=[None, None, ParamPathType.FLAT]
)


def is_empty_or_none(string) -> bool:
    return string is None or len(string) == 0


def to_mac_string(mac_array):
    return None if mac_array is None else ":".join(map(lambda item: f"{item:02X}", mac_array))


def to_utf8_string(data):
    return None if data is None else bytes(data).decode("utf8", errors="ignore")


def to_mac_list(mac_string):
    if mac_string is None:
        return None
    return dbus.Array(map(lambda item: dbus.Byte(int(item, 16)), mac_string.split(":")))


def to_dns_list(string):
    if not string:
        return None
    return dbus.Array(
        [dbus.UInt32(htonl(int(IPv4Interface(s.strip()).network.network_address))) for s in string.split(",")]
    )


def to_dns_string(array):
    if not array:
        return None
    return ",".join([str(IPv4Interface((ntohl(s), "32")).network.network_address) for s in array])


def to_dns_search_list(string):
    if not string:
        return None
    return dbus.Array([s.strip() for s in string.split(",")])


def to_dns_search_string(array):
    if not array:
        return None
    return ",".join(array)


def not_empty_string(val):
    return None if is_empty_or_none(val) else val


def to_string_default_empty(val):
    return "" if val is None else val


def to_dbus_byte_array(val):
    return None if val is None else dbus.ByteArray(val.encode("utf-8"))


def minus_one_is_none(val):
    return None if val == -1 else val


def to_bool_default_true(val) -> bool:
    return bool(val) if val is not None else True


def to_bool_default_false(val) -> bool:
    return bool(val) if val is not None else False


def get_converted_value(value, convert_fn=None):
    return value if convert_fn is None else convert_fn(value)


def get_opt_by_tree_path(data, path: str, default=None):
    path_items = path.split(".")
    obj = data
    for i in range(len(path_items) - 1):
        obj = obj.get(path_items[i])
        if obj is None:
            return default
    return obj.get(path_items[len(path_items) - 1], default)


def set_opt_by_tree_path(data, path: str, value, default_dict):
    dst_path_items = path.split(".")
    last_dst_item = dst_path_items[len(dst_path_items) - 1]
    obj = data
    if value is None:
        for i in range(len(dst_path_items) - 1):
            if dst_path_items[i] not in obj:
                return
            obj = obj[dst_path_items[i]]
        if last_dst_item in obj:
            del obj[last_dst_item]
    else:
        for i in range(len(dst_path_items) - 1):
            obj = obj.setdefault(dst_path_items[i], default_dict)
        obj[last_dst_item] = value


def scan(dev: NMWirelessDevice, scan_timeout: datetime.timedelta) -> None:
    last_scan_ms = dev.get_property("LastScan")
    # nmcli requests scan if last one was more than 30 seconds ago
    if (last_scan_ms == -1) or (time.clock_gettime_ns(time.CLOCK_BOOTTIME) / 1000000 - last_scan_ms >= 30000):
        dev.request_wifi_scan()
        # Documentation says:
        #   To know when the scan is finished, use the "PropertiesChanged" signal
        #   from "org.freedesktop.DBus.Properties" to listen to changes to the "LastScan" property.
        #
        # Simply poll "LastScan"
        start = datetime.datetime.now()
        while start + scan_timeout >= datetime.datetime.now():
            if last_scan_ms != dev.get_property("LastScan"):
                break
            time.sleep(1)
    ssids = set()
    for access_point in dev.get_access_points():
        ssid = to_utf8_string(access_point.get_property("Ssid"))
        if len(ssid) > 0:
            ssids.add(ssid)
    res = list(ssids)
    res.sort()
    return res


def serialize_json_obj(obj) -> str:
    return json.dumps(obj, sort_keys=True)


class JSONSettings:
    def __init__(self, dict_from_json=None) -> None:
        self.params = {} if dict_from_json is None else dict_from_json

    def get_opt(self, path: str, default=None):
        modified_path = path.replace(".", "_")
        res = self.params.get(modified_path)
        return res if res is not None else get_opt_by_tree_path(self.params, path, default)

    def set_value(self, path: str, value, path_type: ParamPathType = ParamPathType.FLAT):
        if path_type == ParamPathType.TREE:
            set_opt_by_tree_path(self.params, path, value, {})
        else:
            if value is not None:
                self.params[path.replace(".", "_")] = value

    def set_opts(self, src: DBUSSettings, params: List[Param]) -> None:
        for param in params:
            self.set_value(
                param.path,
                get_converted_value(src.get_opt(param.path), param.from_dbus),
                param.json_path_type,
            )


class DBUSSettings:
    def __init__(self, dict_from_dbus=None) -> None:
        self.params = dbus.Dictionary(signature="sv") if dict_from_dbus is None else dict_from_dbus

    def get_opt(self, path: str, default=None):
        return get_opt_by_tree_path(self.params, path, default)

    def set_value(self, path: str, value) -> None:
        set_opt_by_tree_path(self.params, path, value, dbus.Dictionary(signature="sv"))

    def set_opts(self, src: JSONSettings, params: List[Param]) -> None:
        for param in params:
            self.set_value(param.path, get_converted_value(src.get_opt(param.path), param.to_dbus))


ipv4_params = [
    Param("ipv4.dhcp-hostname", to_dbus=not_empty_string, json_path_type=ParamPathType.TREE),
    Param("ipv4.dhcp-client-id", to_dbus=not_empty_string, json_path_type=ParamPathType.TREE),
    Param("ipv4.gateway", json_path_type=ParamPathType.TREE),
    Param("ipv4.route-metric", json_path_type=ParamPathType.TREE),
    Param("ipv4.method", json_path_type=ParamPathType.TREE),
    Param("ipv4.dns", to_dbus=to_dns_list, from_dbus=to_dns_string, json_path_type=ParamPathType.TREE),
    Param(
        "ipv4.dns-search",
        to_dbus=to_dns_search_list,
        from_dbus=to_dns_search_string,
        json_path_type=ParamPathType.TREE,
    ),
]


connection_params = [
    Param("connection.uuid", to_dbus=not_empty_string),
    Param("connection.id"),
    Param("connection.interface-name", to_dbus=not_empty_string, from_dbus=to_string_default_empty),
    Param("connection.autoconnect", from_dbus=to_bool_default_true),
]


def set_ipv4_dbus_options(con: DBUSSettings, iface: JSONSettings) -> None:
    # remove deprecated parameter ipv4.addresses
    con.set_value("ipv4.addresses", None)
    con.set_opts(iface, ipv4_params)
    method = iface.get_opt("ipv4.method", "auto")
    ipv4_address = iface.get_opt("ipv4.address")
    ipv4_netmask = iface.get_opt("ipv4.netmask", "255.255.255.0")
    if (method == "manual") or (method == "shared" and ipv4_address is not None):
        net = IPv4Interface(f"{ipv4_address}/{ipv4_netmask}")
        parts = net.with_prefixlen.split("/")
        addr = dbus.Dictionary({"address": parts[0], "prefix": dbus.UInt32(parts[1])})
        con.set_value("ipv4.address-data", dbus.Array([addr], signature=dbus.Signature("a{sv}")))
    else:
        con.set_value("ipv4.address-data", None)


def get_ipv4_dbus_options(res: JSONSettings, cfg: DBUSSettings) -> None:
    res.set_opts(cfg, ipv4_params)
    addr_data = cfg.get_opt("ipv4.address-data")
    if addr_data is not None and len(addr_data) > 0:
        address = addr_data[0].get("address")
        prefix = addr_data[0].get("prefix")
        net = IPv4Interface(f"{address}/{prefix}")
        parts = net.with_netmask.split("/")
        res.set_value("ipv4.address", parts[0], ParamPathType.TREE)
        res.set_value("ipv4.netmask", parts[1], ParamPathType.TREE)


class Connection:
    def __init__(self, dbus_type: str, ui_type: str, additional_params: List[Param]) -> None:
        self.dbus_type = dbus_type
        self.ui_type = ui_type
        self.params = connection_params + additional_params

    def set_dbus_options(self, con: DBUSSettings, iface: JSONSettings) -> SetDbusOptionsResult:
        con.set_opts(iface, self.params)
        set_ipv4_dbus_options(con, iface)
        return {"clear_secrets": False}

    def create(self, iface: JSONSettings) -> dbus.Dictionary:
        con = DBUSSettings()
        self.set_dbus_options(con, iface)
        con.set_value("connection.type", self.dbus_type)
        return con.params

    def can_manage(self, cfg: DBUSSettings):
        user_data = cfg.get_opt("user.data")
        if user_data is not None:
            if to_bool_default_false(user_data.get("wb.read-only")):
                return False

        return cfg.get_opt("connection.type") == self.dbus_type

    @staticmethod
    def get_dbus_settings(con: NMConnection) -> DBUSSettings:
        return DBUSSettings(con.get_settings())

    def get_connection(self, con: NMConnection):
        cfg = self.get_dbus_settings(con)
        if not self.can_manage(cfg):
            return None
        res = JSONSettings()
        res.set_value("type", self.ui_type)
        res.set_opts(cfg, self.params)
        get_ipv4_dbus_options(res, cfg)
        return res.params


class EthernetConnection(Connection):
    def __init__(self) -> None:
        params = [
            Param("802-3-ethernet.mtu"),
        ]
        Connection.__init__(self, "802-3-ethernet", METHOD_ETHERNET, params)


class WiFiDBUSSettings(DBUSSettings):
    def __init__(self, con: NMConnection) -> None:
        super().__init__(con.get_settings())
        self.con = con

    def get_opt(self, path: str, default=None):
        if path == "802-11-wireless-security.psk":
            name = "802-11-wireless-security"
            try:
                return self.con.get_iface().GetSecrets(name)[name]["psk"]
            except dbus.exceptions.DBusException as ex:
                if ex.get_dbus_name() == "org.freedesktop.NetworkManager.Settings.Connection.SettingNotFound":
                    return None
        return super().get_opt(path, default)


class WiFiConnection(Connection):
    def __init__(self, additional_params: List[Param] = None) -> None:
        if additional_params is None:
            additional_params = []
        params = [
            Param("802-11-wireless.mtu"),
            Param("802-11-wireless.ssid", to_dbus_byte_array, to_utf8_string),
            Param("802-11-wireless.mode", from_dbus=lambda v: NM_WIFI_MODE_DEFAULT if v is None else v),
            Param("802-11-wireless-security.key-mgmt", json_path_type=ParamPathType.TREE),
            Param("802-11-wireless-security.psk", json_path_type=ParamPathType.TREE),
        ]
        Connection.__init__(self, "802-11-wireless", METHOD_WIFI, params + additional_params)

    def set_dbus_options(self, con: DBUSSettings, iface: JSONSettings) -> SetDbusOptionsResult:
        res = super().set_dbus_options(con, iface)
        if "802-11-wireless-security" in con.params:
            if iface.get_opt("802-11-wireless-security.security") == "none":
                del con.params["802-11-wireless-security"]
                if con.get_opt("802-11-wireless.security") is not None:
                    del con.params["802-11-wireless"]["security"]

            self.set_encryption(con, iface.get_opt("802-11-wireless-security.encryption"))
        return res

    @staticmethod
    def get_dbus_settings(con: NMConnection) -> DBUSSettings:
        return WiFiDBUSSettings(con)

    def can_manage(self, cfg: DBUSSettings) -> bool:
        return (
            super().can_manage(cfg)
            and (cfg.get_opt("802-11-wireless.mode") == "infrastructure")
            and (
                cfg.get_opt("802-11-wireless-security") is None
                or cfg.get_opt("802-11-wireless-security.key-mgmt") == "wpa-psk"
            )
        )

    def set_encryption(self, con: NMConnection, encryption: str) -> None:
        if encryption == "Auto":
            con.set_value("802-11-wireless-security.pairwise", None)
        elif encryption == "TKIP":
            con.set_value(
                "802-11-wireless-security.pairwise", dbus.Array(["tkip"], signature=dbus.Signature("s"))
            )
        elif encryption == "AES/CCMP":
            con.set_value(
                "802-11-wireless-security.pairwise", dbus.Array(["ccmp"], signature=dbus.Signature("s"))
            )

    def get_encryption(self, con: NMConnection) -> str | None:
        cfg = self.get_dbus_settings(con)
        if cfg.get_opt("802-11-wireless-security.pairwise") is None:
            return "Auto"

        pairwise = list(set(cfg.get_opt("802-11-wireless-security.pairwise")))
        if "tkip" in pairwise and "ccmp" in pairwise:
            return "Auto"
        if "tkip" in pairwise:
            return "TKIP"
        if "ccmp" in pairwise:
            return "AES/CCMP"

        return None

    def get_connection(self, con: NMConnection):
        res = super().get_connection(con)
        if res is not None:
            if "802-11-wireless-security" in res:
                res["802-11-wireless-security"]["security"] = "wpa-psk"
                res["802-11-wireless-security"]["encryption"] = self.get_encryption(con)
            else:
                res["802-11-wireless-security"] = {"security": "none"}
        return res


class WiFiAp(WiFiConnection):
    def __init__(self) -> None:
        params = [
            Param("802-11-wireless.band"),
            Param("802-11-wireless.channel"),
        ]
        WiFiConnection.__init__(self, params)
        self.ui_type = METHOD_WIFI_AP

    def can_manage(self, cfg: DBUSSettings) -> bool:
        return Connection.can_manage(self, cfg) and (cfg.get_opt("802-11-wireless.mode") == "ap")

    def set_dbus_options(self, con: DBUSSettings, iface: JSONSettings) -> SetDbusOptionsResult:
        res = super().set_dbus_options(con, iface)
        con.set_value("802-11-wireless.powersave", 2)
        if "802-11-wireless-security" in con.params:
            # Disable WPS as it can lead to connection problems with MacOS and Linux
            con.set_value("802-11-wireless-security.wps-method", 1)
        user_data = con.get_opt("user.data", dbus.Dictionary(signature="ss"))
        user_data["wb.disable-nat"] = "false" if iface.get_opt("nat", True) else "true"
        con.set_value("user.data", user_data)
        return res

    def get_connection(self, con: NMConnection):
        res = super().get_connection(con)
        if res is not None:
            user_data = self.get_dbus_settings(con).get_opt("user.data")
            if user_data is None:
                res["nat"] = True
            else:
                res["nat"] = user_data.get("wb.disable-nat", "false") == "false"
        return res


class ModemDBUSSettings(DBUSSettings):
    def __init__(self, con: NMConnection) -> None:
        super().__init__(con.get_settings())
        self.con = con

    def get_opt(self, path: str, default=None):
        if path == "gsm.password":
            name = "gsm"
            try:
                return self.con.get_iface().GetSecrets(name)[name]["password"]
            except dbus.exceptions.DBusException as ex:
                if ex.get_dbus_name() == "org.freedesktop.NetworkManager.Settings.Connection.SettingNotFound":
                    return None
            except KeyError:  # for passing tests
                return None
        return super().get_opt(path, default)


class ModemConnection(Connection):
    def __init__(self) -> None:
        params = [
            Param("gsm.sim-slot", from_dbus=minus_one_is_none),
            Param("gsm.apn", to_dbus=not_empty_string, from_dbus=to_string_default_empty),
            Param("gsm.username", to_dbus=not_empty_string, from_dbus=to_string_default_empty),
            Param("gsm.password", to_dbus=not_empty_string, from_dbus=to_string_default_empty),
        ]
        Connection.__init__(self, "gsm", METHOD_MODEM, params)

    def set_dbus_options(self, con: DBUSSettings, iface: JSONSettings) -> SetDbusOptionsResult:
        had_password = con.get_opt("gsm.password") is not None
        res = super().set_dbus_options(con, iface)
        if con.get_opt("gsm.apn"):
            con.set_value("gsm.auto-config", False)
        else:
            con.set_value("gsm.auto-config", True)
        if iface.get_opt("deactivate-by-priority", False):
            user_data = con.get_opt("user.data", dbus.Dictionary(signature="ss"))
            user_data["wb.deactivate-by-priority"] = "true"
            con.set_value("user.data", user_data)
        else:
            user_data = con.get_opt("user.data")
            if user_data is not None and user_data.get("wb.deactivate-by-priority") is not None:
                user_data["wb.deactivate-by-priority"] = "false"
                con.set_value("user.data", user_data)
        # password is removed
        if is_empty_or_none(iface.get_opt("gsm.password")) and had_password:
            res["clear_secrets"] = True
        return res

    def get_connection(self, con: NMConnection):
        res = super().get_connection(con)
        if res is not None:
            user_data = self.get_dbus_settings(con).get_opt("user.data")
            if user_data is None or not res["connection_autoconnect"]:
                res["deactivate-by-priority"] = False
            else:
                res["deactivate-by-priority"] = user_data.get("wb.deactivate-by-priority", "false") == "true"
        return res

    @staticmethod
    def get_dbus_settings(con: NMConnection) -> DBUSSettings:
        return ModemDBUSSettings(con)


def deactivate_connection(network_manager: NetworkManager, connection: NMConnection) -> bool:
    try:
        active_connection = network_manager.get_active_connections().get(connection.get_connection_id())
        if active_connection is None:
            return False
        network_manager.deactivate_connection(active_connection)
        return True
    except dbus.exceptions.DBusException:
        return False


def find_connection_by_uuid(
    json_settings: JSONSettings, network_manager: NetworkManager
) -> Optional[NMConnection]:
    if json_settings.get_opt("connection.uuid"):
        for con in network_manager.get_connections():
            dbus_settings = DBUSSettings(con.get_settings())
            if dbus_settings.get_opt("connection.uuid") == json_settings.get_opt("connection.uuid"):
                return con
    return None


def apply(iface, c_handler, network_manager: NetworkManager, dry_run: bool) -> None:
    if dry_run:
        return
    json_settings = JSONSettings(iface)
    con = find_connection_by_uuid(json_settings, network_manager)
    if con is None:
        network_manager.add_connection(c_handler.create(json_settings))
        return
    old_dump = serialize_json_obj(iface)
    new_dump = serialize_json_obj(c_handler.get_connection(con))
    if old_dump == new_dump:
        return
    dbus_settings = c_handler.get_dbus_settings(con)
    if dbus_settings.get_opt("connection.id") != json_settings.get_opt("connection.id"):
        con.delete()
        network_manager.add_connection(c_handler.create(json_settings))
        return
    set_res = c_handler.set_dbus_options(dbus_settings, json_settings)
    reactivate = deactivate_connection(network_manager, con)
    update_exception = None
    try:
        if set_res["clear_secrets"] is True:
            con.clear_secrets()
        con.update_settings(dbus_settings.params)
    except dbus.exceptions.DBusException as ex:
        update_exception = ex
    if reactivate:
        try:
            network_manager.activate_connection(con, None)
        except dbus.exceptions.DBusException:
            pass
    if update_exception is not None:
        raise update_exception


class NetworkManagerAdapter:
    @staticmethod
    def probe():
        try:
            return NetworkManagerAdapter()
        except dbus.exceptions.DBusException:
            return None

    def __init__(self):
        self.handlers = {
            METHOD_ETHERNET: EthernetConnection(),
            METHOD_MODEM: ModemConnection(),
            METHOD_WIFI: WiFiConnection(),
            METHOD_WIFI_AP: WiFiAp(),
        }
        self.network_manager = NetworkManager()

    def remove_undefined_connections(self, interfaces):
        uids = []
        for iface in interfaces:
            settings = JSONSettings(iface)
            uuid = settings.get_opt("connection.uuid")
            if uuid is not None:
                uids.append(uuid)
        for con in self.network_manager.get_connections():
            c_settings = DBUSSettings(con.get_settings())
            for handler in self.handlers.values():
                if (c_settings.get_opt("connection.uuid") not in uids) and handler.can_manage(c_settings):
                    con.delete()
                    break

    def apply(self, interfaces, dry_run: bool) -> bool:
        if not dry_run:
            self.remove_undefined_connections(interfaces)
        for iface in interfaces:
            handler = self.handlers.get(iface["type"])
            if handler is not None:
                apply(iface, handler, self.network_manager, dry_run)
        return False

    def get_connections(self):
        res = []
        for con in self.network_manager.get_connections():
            for handler in self.handlers.values():
                cfg = handler.get_connection(con)
                if cfg is not None:
                    res.append(cfg)
                    break
        res.sort(key=lambda v: v.get("connection_id", ""))
        return res

    def get_devices(self) -> List[DeviceDesc]:
        devices = []
        type_mapping = {
            NM_DEVICE_TYPE_ETHERNET: DEVICE_TYPE_ETHERNET,
            NM_DEVICE_TYPE_WIFI: DEVICE_TYPE_WIFI,
            NM_DEVICE_TYPE_MODEM: DEVICE_TYPE_MODEM,
        }
        for dev in self.network_manager.get_devices():
            mapping = type_mapping.get(dev.get_property("DeviceType"))
            if mapping:
                iface = dev.get_property("Interface")
                if iface != "dbg0":
                    devices.append({"type": mapping, "iface": iface})
        return devices

    def get_wifi_ssids(self, scan_timeout: datetime.timedelta) -> List[str]:
        free_device = None
        for dev in self.network_manager.get_devices():
            if dev.get_property("DeviceType") == NM_DEVICE_TYPE_WIFI:
                active_cn = dev.get_active_connection()
                if active_cn:
                    # Can scan using devices with active client connection
                    # Scanning on devices with access point can give only one network, so pass them
                    wireless_mode = active_cn.get_connection().get_settings()["802-11-wireless"]["mode"]
                    if wireless_mode == "infrastructure":
                        return scan(NMWirelessDevice(dev), scan_timeout)
                else:
                    if not free_device:
                        free_device = dev
        if free_device:
            return scan(NMWirelessDevice(free_device), scan_timeout)
        return []

    def get_wifi_bands(self) -> List[str]:
        bands = ["bg"]
        has_rtl8723bu = False
        # rtl8723bu driver reports that it supports both 2.4GHz and 5GHz,
        # so we can't rely here on WirelessCapabilities property
        # of org.freedesktop.NetworkManager.Device.Wireless interface
        for dev in self.network_manager.get_devices():
            if dev.get_property("Driver") == "rtl8723bu":
                has_rtl8723bu = True
        if not has_rtl8723bu:
            bands.append("a")
        return bands
