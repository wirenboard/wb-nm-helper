import json
import logging
import struct

import dbus
from connection import Connection, ConnectionState

from wb.nm_helper.connection_manager import (
    CONFIG_FILE,
    ConnectionManager,
    ConnectionManagerConfigFile,
)
from wb.nm_helper.network_manager import NetworkManager


class ActiveConnection:
    def __init__(self, bus, active_connection_path, update_connection_callback, logger):
        self._logger = logger
        self._bus = bus
        self._path = active_connection_path
        self._state = None
        self._connection_path = None
        self._device = None
        self._connectivity = None
        self._ip4addresses = None
        self._update_connection_callback = update_connection_callback

        self._update_handler_match = None
        self._ip4config_update_handler_match = None

        self._logger.info("New active connection %s", self._path)

        # read properties and update fiedls for active connection
        self._update_properties()

        # setup active connection PropertiesChanged handler
        self._update_handler_match = self._bus.add_signal_receiver(
            self._update_handler,
            "PropertiesChanged",
            "org.freedesktop.DBus.Properties",
            "org.freedesktop.NetworkManager",
            self._path,
            sender_keyword="sender",
            destination_keyword="destination",
            interface_keyword="interface",
            path_keyword="path",
            member_keyword="member",
        )

    @property
    def connection_path(self):
        return self._connection_path

    def _format_ip4address_list(self, ip4addresses_list):
        ip4addresses = []
        for ip4address in ip4addresses_list:
            ip4addresses.append(
                ".".join([str(x) for x in struct.unpack("<BBBB", struct.pack("<I", ip4address[0]))])
            )

        return " ".join([x for x in ip4addresses])

    def _enable_ip4config_properties_updating(self):
        self._ip4config_update_handler_match = self._bus.add_signal_receiver(
            self._ip4config_update_handler,
            "PropertiesChanged",
            "org.freedesktop.DBus.Properties",
            "org.freedesktop.NetworkManager",
            self._ip4config_path,
            sender_keyword="sender",
            destination_keyword="destination",
            interface_keyword="interface",
            path_keyword="path",
            member_keyword="member",
        )

    def _disable_ip4config_properties_updating(self):
        if self._ip4config_update_handler_match is not None:
            self._ip4config_update_handler_match.remove()

    def _read_connectivity_state(self):
        with open(CONFIG_FILE, encoding="utf-8") as file:
            config_json = json.load(file)

        network_manager = NetworkManager()
        config = ConnectionManagerConfigFile(network_manager=network_manager)
        config.load_config(config_json)

        active_connection = network_manager.get_active_connections().get(self._name)
        return ConnectionManager.check_connectivity(active_connection, config)

    def _read_properties(self):
        try:
            proxy = self._bus.get_object("org.freedesktop.NetworkManager", self._path)
            interface = dbus.Interface(proxy, "org.freedesktop.DBus.Properties")
            properties = interface.GetAll("org.freedesktop.NetworkManager.Connection.Active")

            self._name = properties["Id"]
            self._uuid = properties["Uuid"]
            self._state = ConnectionState(properties["State"])
            self._connection_path = properties["Connection"]
            self._ip4config_path = properties["Ip4Config"]

            device_path = properties["Devices"][0]
            device_proxy = self._bus.get_object("org.freedesktop.NetworkManager", device_path)
            device_interface = dbus.Interface(device_proxy, "org.freedesktop.DBus.Properties")
            self._device = device_interface.Get("org.freedesktop.NetworkManager.Device", "Interface")

            if self._state == ConnectionState.Activated:
                ip4config_proxy = self._bus.get_object("org.freedesktop.NetworkManager", self._ip4config_path)
                ip4config_interface = dbus.Interface(ip4config_proxy, "org.freedesktop.DBus.Properties")
                ip4addresses_list = ip4config_interface.Get(
                    "org.freedesktop.NetworkManager.IP4Config", "Addresses"
                )
                self._ip4addresses = self._format_ip4address_list(ip4addresses_list)
                self._connectivity = self._read_connectivity_state()

                self._enable_ip4config_properties_updating()
            else:
                self._ip4addresses = None
                self._connectivity = False
                self._disable_ip4config_properties_updating()

        except dbus.exceptions.DBusException:
            self._logger.error("Read active connection %s properties failed", self._path)
            raise

    def _update_properties_manually(self, **new_properties):
        # New properties shold be sent from _update_handler()
        if "state" in new_properties:
            self._state = new_properties["state"]
            self._logger.debug(
                "Set active connection %s %s %s state %s ", self._path, self._name, self._uuid, self._state
            )
            self._update_connection_callback(self._connection_path, state=self._state)

    def _update_properties(self, **new_properties):
        try:
            self._read_properties()
            self._logger.debug(
                "Update active connection %s with %s %s %s %s %s",
                self._path,
                self._name,
                self._uuid,
                self._device,
                self._state,
                self._ip4addresses,
            )

            self._update_connection_callback(
                self._connection_path,
                active=True,
                device=self._device,
                state=self._state,
                ip4addresses=self._ip4addresses,
                connectivity=self._connectivity,
            )
        except dbus.exceptions.DBusException as error:
            # When NetworkManager restarts there is no way to read actual properties
            # If there are new properties from dbus signal they will set manually
            if len(new_properties) != 0:
                self._update_properties_manually(**new_properties)
            else:
                # If properties reading ends unsuccessfully and no new properties from signal
                # new exception rises
                self._logger.error("Update active connection %s failed", self._path)
                raise error

    def _update_handler(self, *args, **kwargs):
        updated_properties = args[1]

        if "State" in updated_properties:
            try:
                self._update_properties(state=ConnectionState(updated_properties["State"]))
            except dbus.exceptions.DBusException:
                self._logger.error("Update active connection properties skipped due to errors %s", self._path)

    def _ip4config_update_handler(self, *args, **kwargs):
        updated_properties = args[1]

        if "Addresses" in updated_properties:
            self._ip4addresses = self._format_ip4address_list(updated_properties["Addresses"])
            self._logger.debug(
                "Set active connection %s %s %s addresses %s ",
                self._path,
                self._name,
                self._uuid,
                self._ip4addresses,
            )
            self._update_connection_callback(self._connection_path, ip4addresses=self._ip4addresses)

    def deactivate(self):
        self._update_handler_match.remove()
        self._update_connection_callback(
            self._connection_path, active=False, state=None, device=None, ip4addresses=None
        )
        self._logger.info("Remove active connection %s", self._path)
