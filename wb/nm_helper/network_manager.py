from __future__ import annotations

from typing import Dict, List, Optional

import dbus

# NMActiveConnectionState
NM_ACTIVE_CONNECTION_STATE_UNKNOWN = 0
NM_ACTIVE_CONNECTION_STATE_ACTIVATING = 1
NM_ACTIVE_CONNECTION_STATE_ACTIVATED = 2
NM_ACTIVE_CONNECTION_STATE_DEACTIVATING = 3
NM_ACTIVE_CONNECTION_STATE_DEACTIVATED = 4

# NMConnectivityState
NM_CONNECTIVITY_UNKNOWN = 0
NM_CONNECTIVITY_NONE = 1
NM_CONNECTIVITY_PORTAL = 2
NM_CONNECTIVITY_LIMITED = 3
NM_CONNECTIVITY_FULL = 4

# from enum NMDeviceType
NM_DEVICE_TYPE_ETHERNET = 1
NM_DEVICE_TYPE_WIFI = 2
NM_DEVICE_TYPE_MODEM = 8

NM_SETTINGS_GSM_SIM_SLOT_DEFAULT = -1

# 802-11-wireless.mode
NM_WIFI_MODE_INFRASTRUCTURE = "infrastructure"
NM_WIFI_MODE_MESH = "mesh"
NM_WIFI_MODE_ADHOC = "adhoc"
NM_WIFI_MODE_AP = "ap"
NM_WIFI_MODE_DEFAULT = NM_WIFI_MODE_INFRASTRUCTURE


def connection_type_to_device_type(cn_type):
    types = {
        "gsm": NM_DEVICE_TYPE_MODEM,
        "802-3-ethernet": NM_DEVICE_TYPE_ETHERNET,
        "802-11-wireless": NM_DEVICE_TYPE_WIFI,
    }
    return types.get(cn_type, 0)


class NMObject:
    def __init__(self, path: str, bus: dbus.SystemBus, interface_name: str):
        self.path = path
        self.bus = bus
        self.interface_name = interface_name
        self.obj = None
        self.iface = None
        self.prop_iface = None
        self.dbus_name = "org.freedesktop.NetworkManager"

    def get_object(self):
        if self.obj is None:
            self.obj = self.bus.get_object(self.dbus_name, self.path)
        return self.obj

    def get_iface(self):
        if self.iface is None:
            self.iface = dbus.Interface(self.get_object(), self.interface_name)
        return self.iface

    def get_prop_iface(self):
        if self.prop_iface is None:
            self.prop_iface = dbus.Interface(self.get_object(), "org.freedesktop.DBus.Properties")
        return self.prop_iface

    def get_property(self, property_name: str):
        return self.get_prop_iface().Get(self.interface_name, property_name)

    def get_path(self) -> str:
        return self.path


class NetworkManager(NMObject):
    def __init__(self):
        NMObject.__init__(
            self,
            "/org/freedesktop/NetworkManager",
            dbus.SystemBus(),
            "org.freedesktop.NetworkManager",
        )

    def find_connection(self, cn_id: str) -> Optional[NMConnection]:
        for c_obj in self.get_connections():
            settings = c_obj.get_settings()
            if str(settings["connection"]["id"]) == cn_id:
                return c_obj
        return None

    def get_active_connections(self) -> Dict[str, NMActiveConnection]:
        res = {}
        for path in self.get_property("ActiveConnections"):
            con = NMActiveConnection(path, self.bus)
            res[con.get_connection_id()] = con
        return res

    def find_device_by_param(self, param_name: str, param_value: str) -> Optional[NMDevice]:
        for device in self.get_devices():
            if device.get_property(param_name) == param_value:
                return device
        return None

    def find_devices_for_connection(self, cn_obj: NMConnection) -> List[NMDevice]:
        settings = cn_obj.get_settings()
        value = settings["connection"].get("interface-name", "")
        if value:
            device = self.find_device_by_param("Interface", value)
            return [device] if device else []
        res = []
        value = connection_type_to_device_type(settings["connection"]["type"])
        for device in self.get_devices():
            if device.get_property("DeviceType") == value:
                res.append(device)
        return res

    def deactivate_connection(self, con: NMActiveConnection) -> None:
        self.get_iface().DeactivateConnection(con.get_object())

    def get_version(self) -> str:
        return self.get_property("Version")

    def get_devices(self) -> List[NMDevice]:
        return map(lambda path: NMDevice(path, self.bus), self.get_iface().GetDevices())

    def get_connections(self) -> List[NMConnection]:
        settings_proxy = self.bus.get_object(
            "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager/Settings"
        )
        settings = dbus.Interface(settings_proxy, "org.freedesktop.NetworkManager.Settings")
        return map(lambda cn_path: NMConnection(cn_path, self.bus), settings.ListConnections())

    def add_connection(self, connection_settings):
        settings_proxy = self.bus.get_object(
            "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager/Settings"
        )
        settings = dbus.Interface(settings_proxy, "org.freedesktop.NetworkManager.Settings")
        settings.AddConnection(connection_settings)

    def activate_connection(self, con: NMConnection, dev: NMDevice) -> NMActiveConnection:
        dev_obj = (
            dev.get_object()
            if dev is not None
            else self.bus.get_object("org.freedesktop.NetworkManager", "/")
        )
        return NMActiveConnection(
            self.get_iface().ActivateConnection(con.get_object(), dev_obj, "/"),
            self.bus,
        )


class NMConnection(NMObject):
    def __init__(self, path: str, bus: dbus.SystemBus):
        NMObject.__init__(self, path, bus, "org.freedesktop.NetworkManager.Settings.Connection")

    def get_connection_id(self) -> str:
        return str(self.get_settings()["connection"]["id"])

    def get_settings(self):
        return self.get_iface().GetSettings()

    def get_sim_slot(self) -> int:
        settings = self.get_settings()
        if "sim-slot" in settings["gsm"]:
            return settings["gsm"]["sim-slot"]
        return NM_SETTINGS_GSM_SIM_SLOT_DEFAULT

    def get_connection_type(self) -> str:
        return str(self.get_settings()["connection"]["type"])

    def get_interface_name(self) -> str:
        return str(self.get_settings()["connection"].get("interface-name", ""))

    def delete(self):
        self.get_iface().Delete()

    def update_settings(self, settings):
        return self.get_iface().Update(settings)

    def clear_secrets(self) -> None:
        self.get_iface().ClearSecrets()


class NMDevice(NMObject):
    def __init__(self, path: str, bus: dbus.SystemBus):
        NMObject.__init__(self, path, bus, "org.freedesktop.NetworkManager.Device")

    def set_metric(self, metric: int):
        props = self.get_iface().GetAppliedConnection(0)
        props[0]["ipv4"]["route-metric"] = dbus.Int64(metric, variant_level=1)
        self.get_iface().Reapply(props[0], 0, 0)

    def get_active_connection(self) -> Optional[NMActiveConnection]:
        cn_path = self.get_property("ActiveConnection")
        if cn_path == "/":
            return None
        return NMActiveConnection(cn_path, self.bus)


class NMActiveConnection(NMObject):
    def __init__(self, path: str, bus: dbus.SystemBus):
        NMObject.__init__(self, path, bus, "org.freedesktop.NetworkManager.Connection.Active")

    def get_devices(self) -> List[NMDevice]:
        res = []
        for dev_path in self.get_property("Devices"):
            dev = NMDevice(dev_path, self.bus)
            res.append(dev)
        return res

    def get_ifaces(self) -> List[str]:
        res = []
        for dev in self.get_devices():
            res.append(dev.get_property("IpInterface"))
        return res

    def get_connection_id(self) -> str:
        return self.get_connection().get_connection_id()

    def get_connection_type(self) -> str:
        return self.get_connection().get_connection_type()

    def get_connection(self) -> NMConnection:
        return NMConnection(self.get_property("Connection"), self.bus)

    def get_ip4_connectivity(self):
        dev_paths = self.get_property("Devices")
        if dev_paths:
            # check only first device and IPv4 connectivity
            dev = NMDevice(dev_paths[0], self.bus)
            return dev.get_property("Ip4Connectivity")
        return NM_CONNECTIVITY_UNKNOWN


class NMAccessPoint(NMObject):
    def __init__(self, path: str, bus: dbus.SystemBus):
        NMObject.__init__(self, path, bus, "org.freedesktop.NetworkManager.AccessPoint")


class NMWirelessDevice(NMObject):
    def __init__(self, dev: NMDevice):
        NMObject.__init__(
            self,
            dev.get_path(),
            dev.bus,
            "org.freedesktop.NetworkManager.Device.Wireless",
        )

    def request_wifi_scan(self) -> None:
        self.get_iface().RequestScan([])

    def get_access_points(self) -> List[NMAccessPoint]:
        return map(
            lambda path: NMAccessPoint(path, self.bus),
            self.get_iface().GetAllAccessPoints(),
        )
