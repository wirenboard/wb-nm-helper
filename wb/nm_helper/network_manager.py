from __future__ import annotations

from typing import Optional

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

    def get_object(self):
        if self.obj is None:
            self.obj = self.bus.get_object("org.freedesktop.NetworkManager", self.path)
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

    def get_path(self):
        return self.path


class NetworkManager(NMObject):
    def __init__(self):
        NMObject.__init__(
            self,
            "/org/freedesktop/NetworkManager",
            dbus.SystemBus(),
            "org.freedesktop.NetworkManager",
        )

    def find_connection(self, cn_id: str) -> NMConnection:
        for c_obj in self.get_connections():
            settings = c_obj.get_settings()
            if str(settings["connection"]["id"]) == cn_id:
                return c_obj
        return None

    def get_active_connections(self) -> dict[str, NMActiveConnection]:
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

    def find_device_for_connection(self, cn_obj: NMConnection) -> Optional[NMDevice]:
        settings = cn_obj.get_settings()
        param = "Interface"
        value = settings["connection"].get("interface-name", "")
        if not value:
            param = "DeviceType"
            value = connection_type_to_device_type(settings["connection"]["type"])
        return self.find_device_by_param(param, value)

    def deactivate_connection(self, con: NMActiveConnection) -> None:
        self.get_iface().DeactivateConnection(con.get_object())

    def get_version(self) -> str:
        return self.get_property("Version")

    def get_devices(self) -> list[NMDevice]:
        return map(lambda path: NMDevice(path, self.bus), self.get_iface().GetDevices())

    def get_connections(self) -> list[NMConnection]:
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
        return NMActiveConnection(
            self.get_iface().ActivateConnection(con.get_object(), dev.get_object(), "/"),
            self.bus,
        )


class NMConnection(NMObject):
    def __init__(self, path: str, bus: dbus.SystemBus):
        NMObject.__init__(self, path, bus, "org.freedesktop.NetworkManager.Settings.Connection")

    def get_settings(self):
        return self.get_iface().GetSettings()

    def delete(self):
        self.get_iface().Delete()

    def update_settings(self, settings):
        return self.get_iface().Update(settings)


class NMDevice(NMObject):
    def __init__(self, path: str, bus: dbus.SystemBus):
        NMObject.__init__(self, path, bus, "org.freedesktop.NetworkManager.Device")

    def get_active_connection(self) -> Optional[NMActiveConnection]:
        cn_path = self.get_property("ActiveConnection")
        if cn_path == "/":
            return None
        return NMActiveConnection(cn_path, self.bus)


class NMActiveConnection(NMObject):
    def __init__(self, path: str, bus: dbus.SystemBus):
        NMObject.__init__(self, path, bus, "org.freedesktop.NetworkManager.Connection.Active")

    def get_ifaces(self) -> list[str]:
        res = []
        for dev_path in self.get_property("Devices"):
            dev = NMDevice(dev_path, self.bus)
            res.append(dev.get_property("IpInterface"))
        return res

    def get_connection_id(self) -> str:
        cn_path = self.get_property("Connection")
        con = NMConnection(cn_path, self.bus)
        settings = con.get_settings()
        return str(settings["connection"]["id"])

    def get_ip4_connectivity(self):
        dev_paths = self.get_property("Devices")
        if len(dev_paths):
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

    def get_access_points(self) -> list[NMAccessPoint]:
        return map(
            lambda path: NMAccessPoint(path, self.bus),
            self.get_iface().GetAllAccessPoints(),
        )
