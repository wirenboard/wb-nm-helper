import argparse
import copy
import enum
import json
import logging
import signal
import struct
import sys
from abc import ABC, abstractmethod
from threading import Lock

import dbus
import dbus.mainloop.glib
import dbus.types
from gi.repository import GLib
from wb_common.mqtt_client import DEFAULT_BROKER_URL, MQTTClient

from wb.nm_helper.connection_manager import check_connectivity
from wb.nm_helper.network_manager import NetworkManager


class Connection(ABC):
    # pylint: disable=too-few-public-methods
    @property
    def properties(self):
        pass


class Event(enum.Enum):
    ACTIVE_INIT = 1
    ACTIVE_UPDATE = 2
    ACTIVE_DEINIT = 3

    COMMON_ACTIVATE = 4
    COMMON_DEACTIVATE = 5


class Mediator(ABC):
    # pylint: disable=too-few-public-methods
    @abstractmethod
    def notify(self, connection: Connection, event: Event):
        pass


class ConnectionsMediator(Mediator):

    DEVICES_UUID_SUBSCRIBE_TOPIC = "/devices/+/controls/UUID"

    def __init__(self, logger) -> None:
        super().__init__()
        self._logger = logger

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        dbus.mainloop.glib.threads_init()
        self._bus = dbus.SystemBus()
        self._loop = GLib.MainLoop()
        self._mqtt_client = MQTTClient("connections-virtual-devices", DEFAULT_BROKER_URL)

        self._common_connections = self._create_common_connections(self._mqtt_client, self._bus)
        self._active_connections = self._create_active_connections(self._bus)

        self._mutex = Lock()

        self._set_connections_event_handlers()

    def run(self):
        self._mqtt_client.start()
        self._run_common_conections()
        self._run_active_connections()
        self._subscribe_to_devices()
        self._loop.run()

    def stop(self):
        self._loop.quit()
        self._mqtt_client.stop()

    def _create_common_connections(self, mqtt_client: MQTTClient, dbus_bus: dbus.Bus):
        connections_settings_proxy = dbus_bus.get_object(
            "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager/Settings"
        )

        connections_settings_interface = dbus.Interface(
            connections_settings_proxy, "org.freedesktop.NetworkManager.Settings"
        )
        connections_paths = connections_settings_interface.ListConnections()

        connections = {}
        for connection_path in connections_paths:
            connections[connection_path] = CommonConnection(
                self, mqtt_client, dbus_bus, connection_path, self._logger
            )
        return connections

    def _run_common_conections(self):
        for common_connection in self._common_connections.values():
            common_connection.run()

    def _create_active_connections(self, dbus_bus: dbus.Bus):
        active_connections_proxy = self._bus.get_object(
            "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager"
        )
        active_connections_interface = dbus.Interface(
            active_connections_proxy, "org.freedesktop.DBus.Properties"
        )
        active_connections_paths = active_connections_interface.Get(
            "org.freedesktop.NetworkManager", "ActiveConnections"
        )

        active_connections = {}
        for active_connection_path in active_connections_paths:
            active_connections[active_connection_path] = ActiveConnection(
                self, dbus_bus, active_connection_path, self._logger
            )
        return active_connections

    def _run_active_connections(self):
        for active_connection in self._active_connections.values():
            active_connection.run()

    def _set_connections_event_handlers(self):
        self._bus.add_signal_receiver(
            self._common_connection_added_handler,
            "NewConnection",
            "org.freedesktop.NetworkManager.Settings",
            "org.freedesktop.NetworkManager",
            "/org/freedesktop/NetworkManager/Settings",
            sender_keyword="sender",
        )
        self._bus.add_signal_receiver(
            self._common_connection_removed_handler,
            "Removed",  # ConnectionRemoved from org.freedesktop.NetworkManager.Settings doesn't work well
            "org.freedesktop.NetworkManager.Settings.Connection",
            "org.freedesktop.NetworkManager",
            None,
            path_keyword="path",
        )
        self._bus.add_signal_receiver(
            self._active_connection_list_update_handler,
            "PropertiesChanged",
            "org.freedesktop.DBus.Properties",
            "org.freedesktop.NetworkManager",
            "/org/freedesktop/NetworkManager",
        )

    # Signals handlers

    def _common_connection_added_handler(self, *args, **kwargs):
        # For some reasons handler receive first signals from non-existed client
        # when you try to do something with connection (add,remove,etc).
        # Finally it receives normal messages after garbage
        connection_path = args[0]
        if kwargs["sender"] in self._bus.list_names():
            with self._mutex:
                self._common_connections[connection_path] = CommonConnection(
                    self, self._mqtt_client, self._bus, connection_path, self._logger
                )

            self._common_connections[connection_path].run()

    def _common_connection_removed_handler(self, *_, **kwargs):
        connection_path = kwargs["path"]

        if connection_path in self._common_connections:
            self._common_connections[connection_path].stop()

            with self._mutex:
                self._common_connections.pop(connection_path)

    def _active_connection_list_update_handler(self, *args, **_):
        updated_properties = args[1]
        if "ActiveConnections" in updated_properties:
            active_connections_paths = updated_properties["ActiveConnections"]
            old_active_paths = [x for x in self._active_connections if x not in active_connections_paths]
            new_active_paths = [x for x in active_connections_paths if x not in self._active_connections]

            for new_active_path in new_active_paths:
                with self._mutex:
                    try:
                        self._active_connections[new_active_path] = ActiveConnection(
                            self, self._bus, new_active_path, self._logger
                        )
                    except dbus.exceptions.DBusException:
                        self._logger.error("New active connection create failed %s", new_active_path)

                if new_active_path in self._active_connections:
                    self._active_connections[new_active_path].run()

            for old_active_path in old_active_paths:
                self._active_connections[old_active_path].stop()

                with self._mutex:
                    self._active_connections.pop(old_active_path)

    def notify(self, connection: Connection, event: Event):
        with self._mutex:
            if connection in self._active_connections.values():
                properties = connection.properties
                if event == Event.ACTIVE_INIT:
                    properties["active"] = True
                elif event == Event.ACTIVE_DEINIT:
                    properties["active"] = False

                if properties["connection_path"] in self._common_connections:
                    self._common_connections[properties["connection_path"]].update(**properties)

            if connection in self._common_connections.values():
                if event == Event.COMMON_ACTIVATE:
                    try:
                        proxy = self._bus.get_object(
                            "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager"
                        )
                        interface = dbus.Interface(proxy, "org.freedesktop.NetworkManager")
                        empty_proxy = self._bus.get_object("org.freedesktop.NetworkManager", "/")
                        interface.ActivateConnection(connection.properties["path"], empty_proxy, empty_proxy)
                    except dbus.exceptions.DBusException:
                        self._logger.error(
                            "Unable to activate %s %s connection, no suitable device found",
                            connection.properties["name"],
                            connection.properties["uuid"],
                        )

                elif event == Event.COMMON_DEACTIVATE:
                    connection_path = connection.properties["path"]

                    active_connections_path = [
                        active_path
                        for active_path, active_connection in self._active_connections.items()
                        if active_connection.properties["connection_path"] == connection_path
                    ]

                    if len(active_connections_path) != 1:
                        self._logger.error("Unable to find active connections to deactivate")
                        return

                    active_connection_path = active_connections_path[0]
                    proxy = self._bus.get_object(
                        "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager"
                    )
                    interface = dbus.Interface(proxy, "org.freedesktop.NetworkManager")
                    interface.DeactivateConnection(active_connection_path)

    def _on_uuid_topic_message(self, _, __, message):
        uuid = message.payload.decode("utf-8")

        if uuid == "":
            return

        with self._mutex:
            existing_connection_paths = [
                connection_path
                for connection_path, connection in self._common_connections.items()
                if connection.properties["uuid"] == uuid
            ]

        if len(existing_connection_paths) == 0:
            self._logger.info("Found old virtual device for %s connection uuid, remove it", uuid)
            CommonConnection.remove_connection_by_uuid(self._mqtt_client, uuid, self._logger)

    def _subscribe_to_devices(self):
        self._mqtt_client.subscribe(self.DEVICES_UUID_SUBSCRIBE_TOPIC)
        self._mqtt_client.message_callback_add(self.DEVICES_UUID_SUBSCRIBE_TOPIC, self._on_uuid_topic_message)


class CommonConnection(Connection):
    NAME_CONTROL_META = {
        "name": "Name",
        "title": {"en": "Name"},
        "type": "text",
        "order": 1,
        "readonly": True,
    }
    UUID_CONTROL_META = {
        "name": "UUID",
        "title": {"en": "UUID"},
        "type": "text",
        "order": 2,
        "readonly": True,
    }
    TYPE_CONTROL_META = {
        "name": "Type",
        "title": {"en": "Type"},
        "type": "text",
        "order": 3,
        "readonly": True,
    }
    ACTIVE_CONTROL_META = {
        "name": "Active",
        "title": {"en": "Active"},
        "type": "switch",
        "order": 4,
        "readonly": True,
    }
    DEVICE_CONTROL_META = {
        "name": "Device",
        "title": {"en": "Device"},
        "type": "text",
        "order": 5,
        "readonly": True,
    }
    STATE_CONTROL_META = {
        "name": "State",
        "title": {"en": "State"},
        "type": "text",
        "order": 6,
        "readonly": True,
    }
    ADDRESS_CONTROL_META = {
        "name": "Address",
        "title": {"en": "IP"},
        "type": "text",
        "order": 7,
        "readonly": True,
    }
    CONNECTIVITY_CONTROL_META = {
        "name": "Connectivity",
        "title": {"en": "Connectivity"},
        "type": "switch",
        "order": 8,
        "readonly": True,
    }
    OPERATOR_CONTROL_META = {
        "name": "Operator",
        "title": {"en": "Operator"},
        "type": "text",
        "order": 9,
        "readonly": True,
    }
    SIGNAL_QUALITY_CONTROL_META = {
        "name": "SignalQuality",
        "title": {"en": "Signal Quality"},
        "type": "text",
        "order": 10,
        "readonly": True,
    }
    ACCESS_TECH_CONTROL_META = {
        "name": "AccessTechnologies",
        "title": {"en": "Access Technologies"},
        "type": "text",
        "order": 11,
        "readonly": True,
    }
    UPDOWN_CONTROL_META = {
        "name": "UpDown",
        "title": {"en": "Up"},
        "type": "pushbutton",
        "order": 12,
        "readonly": False,
    }

    def __init__(
        # pylint: disable=too-many-arguments
        self,
        mediator: Mediator,
        mqtt_client: MQTTClient,
        dbus_bus: dbus.Bus,
        dbus_path,
        logger: logging.Logger,
    ):
        super().__init__()
        self._logger = logger
        self._mediator = mediator
        self._bus = dbus_bus
        self._path = dbus_path
        self._mqtt_client = mqtt_client

        self._properties = {}
        self._updown_control_meta = copy.deepcopy(self.UPDOWN_CONTROL_META)

        self._logger.info("New connection %s", self._path)

    def run(self):
        self._properties = self._read_dbus_settings()
        self._create_virtual_device()

    @property
    def properties(self):
        return {
            "path": self._path,
            "name": self._properties.get("name"),
            "uuid": self._properties.get("uuid"),
        }

    @classmethod
    def remove_connection_by_uuid(cls, mqtt_client: MQTTClient, uuid, logger: logging.Logger):
        connection = CommonConnection(None, mqtt_client, None, None, logger)
        connection._set_uuid_manually(uuid)
        connection._remove_virtual_device()

    def _read_dbus_settings(self):
        proxy = self._bus.get_object("org.freedesktop.NetworkManager", self._path)
        interface = dbus.Interface(proxy, "org.freedesktop.NetworkManager.Settings.Connection")
        dbus_settings = interface.GetSettings()
        result = {}
        result["name"] = str(dbus_settings["connection"]["id"])
        result["uuid"] = str(dbus_settings["connection"]["uuid"])
        result["type"] = str(dbus_settings["connection"]["type"])
        return result

    def _set_uuid_manually(self, uuid):
        self._properties["uuid"] = uuid

    def _get_virtual_device_name(self):
        return "system__networks__" + self._properties.get("uuid")

    def _get_device_topic(self):
        return "/devices/" + self._get_virtual_device_name()

    def _get_control_topic(self, control_meta):
        return self._get_device_topic() + "/controls/" + control_meta.get("name", "")

    def _create_device(self):
        self._mqtt_client.publish(self._get_device_topic(), self._properties.get("uuid"), retain=True)
        self._mqtt_client.publish(
            self._get_device_topic() + "/meta/name",
            "Network Connection " + self._properties.get("name"),
            retain=True,
        )
        self._mqtt_client.publish(self._get_device_topic() + "/meta/driver", "wb-nm-helper", retain=True)

    def _remove_device(self):
        self._mqtt_client.publish(self._get_device_topic() + "/meta/driver", None, retain=True)
        self._mqtt_client.publish(self._get_device_topic() + "/meta/name", None, retain=True)
        self._mqtt_client.publish(self._get_device_topic(), None, retain=True)

    def _create_control(
        self,
        meta,
        value,
    ):
        self._publish_control_meta(meta)
        self._publish_control_data(meta, value)

    def _remove_control(self, meta):
        self._mqtt_client.publish(self._get_control_topic(meta), None, retain=True)
        self._mqtt_client.publish(self._get_control_topic(meta) + "/meta", None, retain=True)

    def _publish_control_data(self, meta, value):
        self._mqtt_client.publish(self._get_control_topic(meta), value, retain=True)

    def _publish_control_meta(self, meta):
        meta_json = json.dumps(meta)
        self._mqtt_client.publish(self._get_control_topic(meta) + "/meta", meta_json, retain=True)

    def _updown_message_callback(self, _, __, ___):
        self._updown_control_meta["readonly"] = True
        self._publish_control_meta(self._updown_control_meta)

        if self._updown_control_meta["title"]["en"] == "Up":
            event = Event.COMMON_ACTIVATE
        else:
            event = Event.COMMON_DEACTIVATE

        self._mediator.notify(self, event)

        self._updown_control_meta["readonly"] = False
        self._publish_control_meta(self._updown_control_meta)

    def _add_control_message_callback(self, meta):
        self._mqtt_client.subscribe(self._get_control_topic(meta) + "/on")
        self._mqtt_client.message_callback_add(
            self._get_control_topic(meta) + "/on", self._updown_message_callback
        )

    def _create_virtual_device(self):
        self._create_device()
        self._create_control(self.NAME_CONTROL_META, self._properties.get("name"))
        self._create_control(self.UUID_CONTROL_META, self._properties.get("uuid"))
        self._create_control(self.TYPE_CONTROL_META, self._properties.get("type"))
        self._create_control(self.ACTIVE_CONTROL_META, "0")
        self._create_control(self.DEVICE_CONTROL_META, None)
        self._create_control(self.STATE_CONTROL_META, None)
        self._create_control(self.ADDRESS_CONTROL_META, None)
        self._create_control(self.CONNECTIVITY_CONTROL_META, "0")
        self._create_control(self._updown_control_meta, None)
        self._add_control_message_callback(self._updown_control_meta)

        if self._properties.get("type") == "gsm":
            self._create_control(self.OPERATOR_CONTROL_META, None)
            self._create_control(self.SIGNAL_QUALITY_CONTROL_META, None)
            self._create_control(self.ACCESS_TECH_CONTROL_META, None)

        self._logger.info(
            "New virtual device %s %s %s",
            self._properties.get("name"),
            self._properties.get("uuid"),
            self._path,
        )

    def _remove_virtual_device(self):
        self._remove_control(self.ACCESS_TECH_CONTROL_META)
        self._remove_control(self.SIGNAL_QUALITY_CONTROL_META)
        self._remove_control(self.OPERATOR_CONTROL_META)
        self._remove_control(self._updown_control_meta)
        self._remove_control(self.CONNECTIVITY_CONTROL_META)
        self._remove_control(self.ADDRESS_CONTROL_META)
        self._remove_control(self.STATE_CONTROL_META)
        self._remove_control(self.DEVICE_CONTROL_META)
        self._remove_control(self.ACTIVE_CONTROL_META)
        self._remove_control(self.TYPE_CONTROL_META)
        self._remove_control(self.UUID_CONTROL_META)
        self._remove_control(self.NAME_CONTROL_META)
        self._remove_device()
        self._logger.info(
            "Remove virtual device %s %s", self._properties.get("name"), self._properties.get("uuid")
        )

    def update(self, **properties):
        if "active" in properties:
            self._publish_control_data(self.ACTIVE_CONTROL_META, "1" if properties["active"] else "0")
            self._updown_control_meta["title"]["en"] = "Down" if properties["active"] else "Up"
            self._publish_control_meta(self._updown_control_meta)
        if "device" in properties:
            self._publish_control_data(self.DEVICE_CONTROL_META, properties["device"])
        if "state" in properties:
            self._publish_control_data(
                self.STATE_CONTROL_META,
                properties["state"].name.lower() if properties["state"] is not None else None,
            )
        if "ip4addresses" in properties:
            self._publish_control_data(self.ADDRESS_CONTROL_META, properties["ip4addresses"])
        if "connectivity" in properties:
            self._publish_control_data(
                self.CONNECTIVITY_CONTROL_META, "1" if properties["connectivity"] else "0"
            )
        if "operator_name" in properties:
            self._publish_control_data(self.OPERATOR_CONTROL_META, properties["operator_name"])
        if "signal_quality" in properties:
            self._publish_control_data(self.SIGNAL_QUALITY_CONTROL_META, properties["signal_quality"])
        if "access_tech" in properties:
            self._publish_control_data(
                self.ACCESS_TECH_CONTROL_META,
                properties["access_tech"].name.replace("MM_MODEM_ACCESS_TECHNOLOGY_", "").lower()
                if properties["access_tech"] is not None
                else None,
            )
        self._logger.debug(
            "Update virtual device settings for %s %s",
            self._properties.get("name"),
            self._properties.get("uuid"),
        )

    def stop(self):
        self._remove_virtual_device()


class ConnectionState(enum.Enum):
    UNKNOWN = 0
    ACTIVATING = 1
    ACTIVATED = 2
    DEACTIVATING = 3
    DEACTIVATED = 4


class ModemAccessTechnology(enum.Enum):
    MM_MODEM_ACCESS_TECHNOLOGY_UNKNOWN = 0
    MM_MODEM_ACCESS_TECHNOLOGY_POTS = 1 << 0
    MM_MODEM_ACCESS_TECHNOLOGY_GSM = 1 << 1
    MM_MODEM_ACCESS_TECHNOLOGY_GSM_COMPACT = 1 << 2
    MM_MODEM_ACCESS_TECHNOLOGY_GPRS = 1 << 3
    MM_MODEM_ACCESS_TECHNOLOGY_EDGE = 1 << 4
    MM_MODEM_ACCESS_TECHNOLOGY_UMTS = 1 << 5
    MM_MODEM_ACCESS_TECHNOLOGY_HSDPA = 1 << 6
    MM_MODEM_ACCESS_TECHNOLOGY_HSUPA = 1 << 7
    MM_MODEM_ACCESS_TECHNOLOGY_HSPA = 1 << 8
    MM_MODEM_ACCESS_TECHNOLOGY_HSPA_PLUS = 1 << 9
    MM_MODEM_ACCESS_TECHNOLOGY_1XRTT = 1 << 10
    MM_MODEM_ACCESS_TECHNOLOGY_EVDO0 = 1 << 11
    MM_MODEM_ACCESS_TECHNOLOGY_EVDOA = 1 << 12
    MM_MODEM_ACCESS_TECHNOLOGY_EVDOB = 1 << 13
    MM_MODEM_ACCESS_TECHNOLOGY_LTE = 1 << 14
    MM_MODEM_ACCESS_TECHNOLOGY_5GNR = 1 << 15
    MM_MODEM_ACCESS_TECHNOLOGY_LTE_CAT_M = 1 << 16
    MM_MODEM_ACCESS_TECHNOLOGY_LTE_NB_IOT = 1 << 17
    MM_MODEM_ACCESS_TECHNOLOGY_ANY = 0xFFFFFFFF


class ActiveConnection(Connection):
    MM_MODEM_STATE_REGISTERED = 11

    def __init__(self, mediator: Mediator, dbus_bus: dbus.Bus, dbus_path, logger: logging.Logger):
        super().__init__()
        self._mediator = mediator
        self._bus = dbus_bus
        self._logger = logger
        self._path = dbus_path

        self._properties = {}

        self._update_handler_match = self._bus.add_signal_receiver(
            self._update_handler,
            "PropertiesChanged",
            "org.freedesktop.DBus.Properties",
            "org.freedesktop.NetworkManager",
            self._path,
        )
        self._ip4config_update_handler_match = None

    @property
    def properties(self):
        result = {"path": self._path}
        for key in ["connection_path", "state", "device", "ip4addresses", "connectivity"]:
            result[key] = self._properties.get(key)

        if self._properties.get("type") == "gsm":
            for key in ["operator_name", "signal_quality", "access_tech"]:
                result[key] = self._properties.get(key)
        return result

    def run(self):
        self._properties = self._read_properties()
        self._ip4config_update_handler_match = self._enable_ip4config_properties_updating(
            self._properties["state"]
        )
        self._logger.info(
            "New active connection %s %s %s %s",
            self._properties["name"],
            self._properties["uuid"],
            self._properties["connection_path"],
            self._path,
        )

        self._mediator.notify(self, Event.ACTIVE_INIT)

    def _format_ip4address_list(self, ip4addresses_list):
        ip4addresses = []
        for ip4address in ip4addresses_list:
            ip4addresses.append(
                ".".join([str(x) for x in struct.unpack("<BBBB", struct.pack("<I", ip4address[0]))])
            )

        return " ".join(ip4addresses)

    def _enable_ip4config_properties_updating(self, state: ConnectionState):
        if state == ConnectionState.ACTIVATED:
            return self._bus.add_signal_receiver(
                self._ip4config_update_handler,
                "PropertiesChanged",
                "org.freedesktop.DBus.Properties",
                "org.freedesktop.NetworkManager",
                self._properties["ip4config_path"],
            )
        return None

    def _disable_ip4config_properties_updating(self):
        if self._ip4config_update_handler_match is not None:
            self._ip4config_update_handler_match.remove()

    def _read_connectivity_state(self, name):
        network_manager = NetworkManager()
        active_connection = network_manager.get_active_connections().get(name)
        return check_connectivity(active_connection)

    def _read_properties(self):
        try:
            proxy = self._bus.get_object("org.freedesktop.NetworkManager", self._path)
            interface = dbus.Interface(proxy, "org.freedesktop.DBus.Properties")
            properties = interface.GetAll("org.freedesktop.NetworkManager.Connection.Active")

            result = {}
            result["name"] = properties["Id"]
            result["uuid"] = properties["Uuid"]
            result["type"] = properties["Type"]
            result["state"] = ConnectionState(properties["State"])
            result["connection_path"] = properties["Connection"]
            result["ip4config_path"] = properties["Ip4Config"]

            result["device"] = None
            if len(properties["Devices"]) > 0:
                device_path = properties["Devices"][0]
                device_proxy = self._bus.get_object("org.freedesktop.NetworkManager", device_path)
                device_interface = dbus.Interface(device_proxy, "org.freedesktop.DBus.Properties")
                result["device"] = device_interface.Get("org.freedesktop.NetworkManager.Device", "Interface")

            result["ip4addresses"] = None
            result["connectivity"] = False
            if result["state"] == ConnectionState.ACTIVATED:
                proxy = self._bus.get_object("org.freedesktop.NetworkManager", result["ip4config_path"])
                interface = dbus.Interface(proxy, "org.freedesktop.DBus.Properties")
                ip4addresses_list = interface.Get("org.freedesktop.NetworkManager.IP4Config", "Addresses")

                result["ip4addresses"] = self._format_ip4address_list(ip4addresses_list)
                result["connectivity"] = self._read_connectivity_state(result["name"])

            if result["type"] == "gsm":
                result["operator_name"] = None
                result["signal_quality"] = None
                result["access_tech"] = None

                proxy = self._bus.get_object(
                    "org.freedesktop.ModemManager1", "/org/freedesktop/ModemManager1"
                )
                interface = dbus.Interface(proxy, "org.freedesktop.DBus.ObjectManager")
                modem_manager_objects = interface.GetManagedObjects()
                modem_paths = modem_manager_objects.keys()

                for modem_path in modem_paths:
                    proxy = self._bus.get_object("org.freedesktop.ModemManager1", modem_path)
                    interface = dbus.Interface(proxy, "org.freedesktop.DBus.Properties")
                    modem_properties = interface.GetAll("org.freedesktop.ModemManager1.Modem")

                    if (
                        modem_properties.get("PrimaryPort") == result["device"]
                        and modem_properties.get("State") == self.MM_MODEM_STATE_REGISTERED
                    ):
                        interface = dbus.Interface(proxy, "org.freedesktop.ModemManager1.Modem.Simple")
                        status = interface.GetStatus()

                        result["operator_name"] = status.get("m3gpp-operator-name")
                        result["signal_quality"] = status.get("signal-quality")[0]
                        result["access_tech"] = ModemAccessTechnology(status.get("access-technologies"))

                        break

            return result
        except dbus.exceptions.DBusException:
            self._logger.error("Read active connection %s properties failed", self._path)
            raise

    def _update_handler(self, *args, **_):
        updated_properties = args[1]

        if "State" in updated_properties:
            try:
                self._properties = self._read_properties()
            except dbus.exceptions.DBusException:
                self._properties["state"] = ConnectionState(updated_properties["State"])

            self._logger.debug("Set state %s for %s connection", self._properties["state"], self._path)
            self._ip4config_update_handler_match = self._enable_ip4config_properties_updating(
                self._properties["state"]
            )
            self._mediator.notify(self, Event.ACTIVE_UPDATE)

    def _ip4config_update_handler(self, *args, **_):
        updated_properties = args[1]

        if "Addresses" in updated_properties:
            self._properties["ip4addresses"] = self._format_ip4address_list(updated_properties["Addresses"])
            self._logger.debug(
                "Set address %s for %s connection", self._properties["ip4addresses"], self._path
            )
            self._mediator.notify(self, Event.ACTIVE_UPDATE)

    def stop(self):
        self._update_handler_match.remove()
        self._disable_ip4config_properties_updating()
        self._logger.info("Remove active connection %s", self._path)

        # reset some properties manually
        self._properties["state"] = None
        self._properties["device"] = None
        self._properties["ip4addresses"] = None
        self._properties["connectivity"] = False

        if self._properties["type"] == "gsm":
            self._properties["operator_name"] = None
            self._properties["signal_quality"] = None
            self._properties["access_tech"] = None

        self._mediator.notify(self, Event.ACTIVE_DEINIT)


def main():
    parser = argparse.ArgumentParser(description="Service for creating virtual connection devices")
    parser.add_argument(
        "--debug",
        help="Enable debug output",
        default=False,
        dest="debug",
        required=False,
        action="store_true",
    )
    options = parser.parse_args()

    if options.debug:
        logger_level = logging.DEBUG
    else:
        logger_level = logging.INFO

    # logger = logging.getLogger("virtual-connections_client")
    # logger.setLevel(logger_level)
    # stream = logging.StreamHandler()
    # stream.setLevel(logger_level)
    # logger.addHandler(stream)
    logging.basicConfig(level=logger_level)

    connections_mediator = ConnectionsMediator(logging)

    def stop_virtual_connections_client(_, __):
        connections_mediator.stop()

    signal.signal(signal.SIGINT, stop_virtual_connections_client)
    signal.signal(signal.SIGTERM, stop_virtual_connections_client)

    try:
        connections_mediator.run()
    except (KeyboardInterrupt, dbus.exceptions.DBusException):
        pass
    finally:
        logging.info("Stopping")
        connections_mediator.stop()


if __name__ == "__main__":
    sys.exit(main())
