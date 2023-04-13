import argparse
import asyncio
import copy
import enum
import json
import logging
import signal
import struct
import sys
import threading
from abc import ABC, abstractmethod

import dbus
import dbus.mainloop.glib
import dbus.types
from gi.repository import GLib
from wb_common.mqtt_client import DEFAULT_BROKER_URL, MQTTClient

from wb.nm_helper.connection_manager import check_connectivity
from wb.nm_helper.network_manager import NetworkManager


class EventLoop:
    def __init__(self):
        self._event_loop = asyncio.new_event_loop()
        self._event_loop_thread = threading.Thread(target=lambda: self._run_event_loop(self._event_loop))

    def run(self):
        self._event_loop_thread.start()

    def stop(self):
        if self._event_loop_thread.is_alive():
            asyncio.run_coroutine_threadsafe(self._stop_event_loop(self._event_loop), self._event_loop)
            self._event_loop_thread.join()

    def _run_event_loop(self, event_loop):
        asyncio.set_event_loop(event_loop)
        event_loop.run_forever()

    async def _stop_event_loop(self, event_loop):
        await self._event_loop.shutdown_asyncgens()
        event_loop.stop()

    def run_coroutine_threadsafe(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self._event_loop)


class Connection(ABC):
    # pylint: disable=too-few-public-methods
    @property
    def properties(self):
        pass


class EventType(enum.Enum):
    COMMON_CREATE = 1
    COMMON_SWITCH = 2
    COMMON_REMOVE = 3

    ACTIVE_PROPERTIES_UPDATED = 4
    ACTIVE_CONNECTIVITY_UPDATED = 5
    ACTIVE_MODEM_STATE_UPDATED = 6

    ACTIVE_LIST_UPDATE = 7
    MQTT_UUID_PUBLICATED = 8
    CONNECTIVITY_REQUEST = 9


class Event:
    events_count = 0

    def __init__(self, event_type: EventType, **kwargs):
        self._type = event_type
        Event.events_count = (Event.events_count + 1) % 1000  # set numbers ceiling for logging readability
        self._number = Event.events_count
        self._kwargs = kwargs
        logging.debug("New event %s %s", self._number, self._type.name)

    @property
    def type(self):
        return self._type

    @property
    def number(self):
        return self._number

    @property
    def kwargs(self):
        return self._kwargs


class Mediator(ABC):
    # pylint: disable=too-few-public-methods
    @abstractmethod
    def new_event(self, event: Event):
        pass


class ConnectionsMediator(Mediator):

    DEVICES_UUID_SUBSCRIBE_TOPIC = "/devices/+/controls/UUID"

    def __init__(self, broker) -> None:
        super().__init__()
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        dbus.mainloop.glib.threads_init()
        self._bus = dbus.SystemBus()
        self._dbus_loop = GLib.MainLoop()
        self._mqtt_client = MQTTClient("connections-virtual-devices", broker)

        self._common_connections = {}
        self._active_connections = {}
        self._event_loop = EventLoop()
        self._connectivity_updater = ConnectivityUpdater(self)

        self._set_connections_event_handlers()

    def run(self):
        self._mqtt_client.start()
        self._event_loop.run()
        self._connectivity_updater.run()

        self._create_common_connections()
        self._create_active_connections()
        self._subscribe_to_devices()

        self._dbus_loop.run()

    def stop(self):
        self._event_loop.stop()
        self._connectivity_updater.stop()
        self._dbus_loop.quit()
        self._mqtt_client.stop()

    # Signals handlers

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
            self._active_list_update_handler,
            "PropertiesChanged",
            "org.freedesktop.DBus.Properties",
            "org.freedesktop.NetworkManager",
            "/org/freedesktop/NetworkManager",
        )

    def _create_common_connections(self):
        connections_settings_proxy = self._bus.get_object(
            "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager/Settings"
        )

        connections_settings_interface = dbus.Interface(
            connections_settings_proxy, "org.freedesktop.NetworkManager.Settings"
        )
        connections_paths = connections_settings_interface.ListConnections()

        for connection_path in connections_paths:
            self.new_event(Event(EventType.COMMON_CREATE, path=connection_path))

    def _create_active_connections(self):
        active_connections_proxy = self._bus.get_object(
            "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager"
        )
        active_connections_interface = dbus.Interface(
            active_connections_proxy, "org.freedesktop.DBus.Properties"
        )
        active_connections_paths = active_connections_interface.Get(
            "org.freedesktop.NetworkManager", "ActiveConnections"
        )

        self.new_event(Event(EventType.ACTIVE_LIST_UPDATE, path_list=active_connections_paths))

    def _on_uuid_topic_message(self, _, __, message):
        uuid = message.payload.decode("utf-8")

        if uuid == "":
            return

        self.new_event(Event(EventType.MQTT_UUID_PUBLICATED, uuid=uuid))

    def _subscribe_to_devices(self):
        self._mqtt_client.subscribe(self.DEVICES_UUID_SUBSCRIBE_TOPIC)
        self._mqtt_client.message_callback_add(self.DEVICES_UUID_SUBSCRIBE_TOPIC, self._on_uuid_topic_message)

    def _common_connection_added_handler(self, *args, **kwargs):
        # For some reasons handler receive first signals from non-existed client
        # when you try to do something with connection (add,remove,etc).
        # Finally it receives normal messages after garbage
        if kwargs["sender"] in self._bus.list_names():
            self.new_event(Event(EventType.COMMON_CREATE, path=args[0]))

    def _common_connection_removed_handler(self, *_, **kwargs):
        self.new_event(Event(EventType.COMMON_REMOVE, path=kwargs["path"]))

    def _active_list_update_handler(self, *args, **_):
        updated_properties = args[1]
        if "ActiveConnections" in updated_properties:
            self.new_event(
                Event(
                    EventType.ACTIVE_LIST_UPDATE,
                    path_list=updated_properties["ActiveConnections"],
                )
            )

    # Async event functions

    def _common_connection_create(self, connection_path):
        if connection_path is None:
            return
        try:
            new_common_connection = CommonConnection(self, self._mqtt_client, self._bus, connection_path)
            new_common_connection.run()
            self._common_connections[connection_path] = new_common_connection
        except dbus.exceptions.DBusException:
            logging.error("Common connection %s creation failed", connection_path)

    def _common_connection_switch(self, connection: Connection):
        connection_path = connection.properties["path"]
        if connection_path not in self._common_connections:
            return

        connection.set_updown_button_readonly(True)

        active_connections_path = [
            active_path
            for active_path, active_connection in self._active_connections.items()
            if active_connection.properties["connection"] == connection_path
        ]

        if len(active_connections_path) == 0:
            try:
                proxy = self._bus.get_object(
                    "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager"
                )
                interface = dbus.Interface(proxy, "org.freedesktop.NetworkManager")
                empty_proxy = self._bus.get_object("org.freedesktop.NetworkManager", "/")
                # ActivateConnection and DeactivateConnection functions ends very fast
                # even if connection activating/deactivating process can take a long time
                interface.ActivateConnection(connection.properties["path"], empty_proxy, empty_proxy)
            except dbus.exceptions.DBusException:
                logging.error(
                    "Unable to activate %s %s connection, no suitable device found",
                    connection.properties["name"],
                    connection.properties["uuid"],
                )
                # this is for interface
                connection.update({"state": None})

        elif len(active_connections_path) == 1:
            active_connection = self._active_connections[active_connections_path[0]]
            try:
                proxy = self._bus.get_object(
                    "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager"
                )
                interface = dbus.Interface(proxy, "org.freedesktop.NetworkManager")
                # ActivateConnection and DeactivateConnection functions ends very fast
                # even if connection activating/deactivating process can take a long time
                interface.DeactivateConnection(active_connection.properties["path"])
            except dbus.exceptions.DBusException:
                logging.error("The connection %s was not active", connection.properties["path"])
        else:
            logging.error("Unable to find connection to switch")

        connection.set_updown_button_readonly(False)

    def _common_connection_remove(self, connection_path):
        if connection_path is not None and connection_path in self._common_connections:
            self._common_connections[connection_path].stop()
            self._common_connections.pop(connection_path)

    def _active_connections_list_update(self, active_connections_paths):
        if active_connections_paths is None:
            return

        old_active_paths = [x for x in self._active_connections if x not in active_connections_paths]
        new_active_paths = [x for x in active_connections_paths if x not in self._active_connections]

        for new_active_path in new_active_paths:
            try:
                new_active_connection = ActiveConnection(self, self._bus, new_active_path)
                new_active_connection.run()

                connection_path = new_active_connection.properties["connection"]
                common_connection = self._common_connections[connection_path]
                common_connection.update(new_active_connection.properties)

                self._connectivity_updater.update(new_active_connection)

                self._active_connections[new_active_path] = new_active_connection
            except dbus.exceptions.DBusException:
                # When connection up/down/create/remove is in process, active connectins list
                # changes very fast and it's impossible to create some temporary active connections
                # because they are removing faster than we can read their properties.
                # Finally, when some active connection becomes stable,
                # we can successfully read its properties on active connections list update signal.
                # So this message mostly for debug
                logging.debug("New active connection create failed %s", new_active_path)

        for old_active_path in old_active_paths:
            old_active_connection = self._active_connections[old_active_path]
            old_active_connection.stop()

            connection_path = old_active_connection.properties["connection"]
            common_connection = self._common_connections[connection_path]
            common_connection.update(old_active_connection.properties)

            self._active_connections.pop(old_active_path)

    def _active_connection_connectivity_updated(self, connection: Connection, connectivity):
        if connection is None or connectivity is None:
            return

        connection_path = connection.properties["connection"]
        active_connection_path = connection.properties["path"]
        if connection_path in self._common_connections and active_connection_path in self._active_connections:
            self._common_connections[connection_path].update({"connectivity": connectivity})

    # ActiveConnection have been updated by dbus
    def _active_connection_properties_updated(self, connection: Connection, properties):
        if connection in self._active_connections.values():
            connection.update(properties)

            connection_path = connection.properties["connection"]
            if connection_path in self._common_connections:
                self._common_connections[connection_path].update(connection.properties)

                if connection.properties["state"] == ConnectionState.ACTIVATED:
                    self._connectivity_updater.update(connection)
                else:
                    self._common_connections[connection_path].update({"connectivity": False})

    def _active_connection_modem_state_updated(self, connection):
        if connection in self._active_connections:
            connection.update_modem()

    def _common_connection_check_uuid(self, uuid):
        if uuid is None:
            return

        existing_connection_paths = [
            connection_path
            for connection_path, connection in self._common_connections.items()
            if connection.properties["uuid"] == uuid
        ]

        if len(existing_connection_paths) == 0:
            logging.info("Found old virtual device for %s connection uuid, remove it", uuid)
            CommonConnection.remove_connection_by_uuid(self._mqtt_client, uuid)

    async def _run_async_event(self, event: Event):
        logging.debug("Execute event %s %s", event.number, event.type.name)
        if event.type == EventType.COMMON_CREATE:
            self._common_connection_create(event.kwargs.get("path"))

        elif event.type == EventType.COMMON_SWITCH:
            self._common_connection_switch(event.kwargs.get("connection"))

        elif event.type == EventType.COMMON_REMOVE:
            self._common_connection_remove(event.kwargs.get("path"))

        elif event.type == EventType.ACTIVE_LIST_UPDATE:
            self._active_connections_list_update(event.kwargs.get("path_list"))

        elif event.type == EventType.ACTIVE_CONNECTIVITY_UPDATED:
            self._active_connection_connectivity_updated(
                event.kwargs.get("connection"), event.kwargs.get("connectivity")
            )

        elif event.type == EventType.ACTIVE_PROPERTIES_UPDATED:
            self._active_connection_properties_updated(
                event.kwargs.get("connection"), event.kwargs.get("properties")
            )

        elif event.type == EventType.ACTIVE_MODEM_STATE_UPDATED:
            self._active_connection_modem_state_updated(event.kwargs.get("connection"))

        elif event.type == EventType.MQTT_UUID_PUBLICATED:
            self._common_connection_check_uuid(event.kwargs.get("uuid"))

    def new_event(self, event: Event):
        self._event_loop.run_coroutine_threadsafe(self._run_async_event(event))


class ConnectivityUpdater:
    def __init__(self, mediator: Mediator):
        self._mediator = mediator
        self._network_manager = None
        self._event_loop = EventLoop()
        self._futures = {}

    def run(self):
        self._event_loop.run()

    def stop(self):
        self._event_loop.stop()

    def update(self, connection: Connection):
        name = connection.properties.get("name")
        if name is None:
            return

        if name in self._futures:
            self._futures[name].cancel()

        self._futures[name] = self._event_loop.run_coroutine_threadsafe(
            self._run_async_event(Event(EventType.CONNECTIVITY_REQUEST, connection=connection))
        )

    async def _run_async_event(self, event: Event):

        logging.debug("Execute event %s %s", event.number, event.type.name)
        self._network_manager = NetworkManager()

        connection = event.kwargs.get("connection")
        if connection is None:
            return

        try:
            name = connection.properties.get("name")
            active_connection = self._network_manager.get_active_connections().get(name)
            if active_connection is not None:
                connectivity = check_connectivity(active_connection)

                self._mediator.new_event(
                    Event(
                        EventType.ACTIVE_CONNECTIVITY_UPDATED,
                        connection=connection,
                        connectivity=connectivity,
                    )
                )
        except dbus.exceptions.DBusException:
            logging.error("Unable to read connectivity for %s", connection.properties.get("path"))


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
        self,
        mediator: Mediator,
        mqtt_client: MQTTClient,
        dbus_bus: dbus.Bus,
        dbus_path,
    ):
        super().__init__()
        self._mediator = mediator
        self._bus = dbus_bus
        self._path = dbus_path
        self._mqtt_client = mqtt_client

        self._properties = {}
        self._updown_control_meta = copy.deepcopy(self.UPDOWN_CONTROL_META)

        logging.info("New connection %s", self._path)

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
    def remove_connection_by_uuid(cls, mqtt_client: MQTTClient, uuid):
        connection = CommonConnection(None, mqtt_client, None, None)
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
        self._mediator.new_event(Event(EventType.COMMON_SWITCH, connection=self))

    def set_updown_button_readonly(self, readonly):
        self._updown_control_meta["readonly"] = readonly
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

        logging.info(
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
        logging.info(
            "Remove virtual device %s %s %s",
            self._properties.get("name"),
            self._properties.get("uuid"),
            self._path,
        )

    def update(self, properties):
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
                properties["access_tech"].name.replace("MM_MODEM_ACCESS_TECHNOLOGY_", "").upper()
                if properties["access_tech"] is not None
                else None,
            )
        logging.debug(
            "Update virtual device settings for %s %s %s %s",
            self._properties.get("name"),
            self._properties.get("uuid"),
            self._path,
            list(properties.keys()),
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

    def __init__(self, mediator: Mediator, dbus_bus: dbus.Bus, dbus_path):
        super().__init__()
        self._mediator = mediator
        self._bus = dbus_bus
        self._path = dbus_path

        self._properties = {}

        self._update_handler_match = None
        self._ip4config_update_handler_match = None
        self._modem_update_handler_match = None

    @property
    def properties(self):
        result = {"path": self._path}
        for key in ["connection", "name", "uuid", "active", "state", "device", "ip4addresses"]:
            result[key] = self._properties.get(key)

        if self._properties.get("type") == "gsm":
            for key in ["operator_name", "signal_quality", "access_tech"]:
                result[key] = self._properties.get(key)
        return result

    def run(self):
        self._update_handler_match = self._bus.add_signal_receiver(
            self._update_handler,
            "PropertiesChanged",
            "org.freedesktop.DBus.Properties",
            "org.freedesktop.NetworkManager",
            self._path,
        )

        self.update(self._read_properties())

        logging.info(
            "New active connection %s %s %s %s",
            self.properties["name"],
            self.properties["uuid"],
            self.properties["connection"],
            self._path,
        )

    def update(self, properties):
        self._properties.update(properties)

        if "device" in properties and self._properties.get("type") == "gsm":
            modem_path = self._find_modem_path_by_device(properties["device"])
            self._properties["modem_path"] = modem_path
            self._properties.update(self._read_modem_properties(modem_path))
            self._update_modem_handlers(modem_path)

        if "ip4config_path" in properties:
            self._update_ip4config_handlers(properties["ip4config_path"])

    def update_modem(self):
        modem_path = self._properties["modem_path"]
        self._properties.update(self._read_modem_properties(modem_path))

    def _update_modem_handlers(self, modem_path):
        if self._modem_update_handler_match is not None:
            self._modem_update_handler_match.remove()
            self._modem_update_handler_match = None

        if modem_path is not None:
            self._modem_update_handler_match = self._bus.add_signal_receiver(
                self._modem_update_handler,
                "StateChanged",
                "org.freedesktop.ModemManager1.Modem",
                "org.freedesktop.ModemManager1",
                modem_path,
            )

    def _update_ip4config_handlers(self, ip4config_path):
        if self._ip4config_update_handler_match is not None:
            self._ip4config_update_handler_match.remove()
            self._ip4config_update_handler_match = None

        if ip4config_path != "/":
            self._ip4config_update_handler_match = self._bus.add_signal_receiver(
                self._update_handler,
                "PropertiesChanged",
                "org.freedesktop.DBus.Properties",
                "org.freedesktop.NetworkManager",
                ip4config_path,
            )

    def _read_properties(self):
        try:
            proxy = self._bus.get_object("org.freedesktop.NetworkManager", self._path)
            interface = dbus.Interface(proxy, "org.freedesktop.DBus.Properties")
            properties = interface.GetAll("org.freedesktop.NetworkManager.Connection.Active")

            result = {"active": True}
            result.update(self._parse_dbus_properties(properties))
            return result
        except dbus.exceptions.DBusException:
            # Plase read message about ActiveConnection creation process in
            # active connections list update event handler
            logging.debug(
                "Read active connection %s %s properties failed",
                self.properties["connection"],
                self._path,
            )
            raise

    def _find_modem_path_by_device(self, device):
        path = None
        if device is not None:
            proxy = self._bus.get_object("org.freedesktop.ModemManager1", "/org/freedesktop/ModemManager1")
            interface = dbus.Interface(proxy, "org.freedesktop.DBus.ObjectManager")
            modem_manager_objects = interface.GetManagedObjects()
            modem_paths = modem_manager_objects.keys()

            for modem_path in modem_paths:
                proxy = self._bus.get_object("org.freedesktop.ModemManager1", modem_path)
                interface = dbus.Interface(proxy, "org.freedesktop.DBus.Properties")
                modem_port = interface.Get("org.freedesktop.ModemManager1.Modem", "PrimaryPort")
                if modem_port == device:
                    path = modem_path
                    break
        return path

    def _read_modem_properties(self, modem_path):
        result = {}
        if modem_path is not None:
            proxy = self._bus.get_object("org.freedesktop.ModemManager1", modem_path)
            interface = dbus.Interface(proxy, "org.freedesktop.ModemManager1.Modem.Simple")
            status_properties = interface.GetStatus()
            result.update(self._parse_dbus_properties(status_properties))
        return result

    def _parse_dbus_properties(self, dbus_properties):
        # pylint: disable=too-many-branches
        result = {}
        if "Id" in dbus_properties:
            result["name"] = dbus_properties["Id"]
        if "Uuid" in dbus_properties:
            result["uuid"] = dbus_properties["Uuid"]
        if "Type" in dbus_properties:
            result["type"] = dbus_properties["Type"]
        if "State" in dbus_properties:
            result["state"] = ConnectionState(dbus_properties["State"])
        if "Connection" in dbus_properties:
            result["connection"] = dbus_properties["Connection"]

        if "Ip4Config" in dbus_properties:
            result["ip4config_path"] = dbus_properties["Ip4Config"]
            result["ip4addresses"] = None
            if dbus_properties["Ip4Config"] != "/":
                proxy = self._bus.get_object("org.freedesktop.NetworkManager", dbus_properties["Ip4Config"])
                interface = dbus.Interface(proxy, "org.freedesktop.DBus.Properties")
                ip4addresses_list = interface.Get("org.freedesktop.NetworkManager.IP4Config", "Addresses")
                result["ip4addresses"] = self._format_ip4address_list(ip4addresses_list)

        if "Devices" in dbus_properties:
            result["device"] = None
            if len(dbus_properties["Devices"]) > 0:
                device_path = dbus_properties["Devices"][0]
                device_proxy = self._bus.get_object("org.freedesktop.NetworkManager", device_path)
                device_interface = dbus.Interface(device_proxy, "org.freedesktop.DBus.Properties")
                result["device"] = device_interface.Get("org.freedesktop.NetworkManager.Device", "Interface")
        if "Addresses" in dbus_properties:
            result["ip4addresses"] = self._format_ip4address_list(dbus_properties["Addresses"])

        if "access-technologies" in dbus_properties:
            result["access_tech"] = ModemAccessTechnology(dbus_properties["access-technologies"])
        if "signal-quality" in dbus_properties:
            result["signal_quality"] = dbus_properties["signal-quality"][0]
        if "m3gpp-operator-name" in dbus_properties:
            result["operator_name"] = dbus_properties["m3gpp-operator-name"]
        return result

    def _format_ip4address_list(self, ip4addresses_list):
        ip4addresses = []
        for ip4address in ip4addresses_list:
            ip4addresses.append(
                ".".join([str(x) for x in struct.unpack("<BBBB", struct.pack("<I", ip4address[0]))])
            )
        unical_ip4addresses = list(set(ip4addresses))

        return " ".join(unical_ip4addresses)

    def _update_handler(self, *args, **_):
        new_properties = self._parse_dbus_properties(args[1])
        if new_properties:
            self._mediator.new_event(
                Event(EventType.ACTIVE_PROPERTIES_UPDATED, connection=self, properties=new_properties)
            )

    def _modem_update_handler(self, *_, **__):
        self._mediator.new_event(Event(EventType.ACTIVE_MODEM_STATE_UPDATED, connection=self))

    def stop(self):
        empty_properties = {
            "active": False,
            "state": None,
            "device": None,
            "ip4config_path": "/",
            "ip4addresses": None,
            "operator_name": None,
            "signal_quality": None,
            "access_tech": None,
        }
        self.update(empty_properties)
        self._update_handler_match.remove()

        logging.info("Remove active connection %s %s", self.properties["connection"], self._path)


def main():
    parser = argparse.ArgumentParser(description="Service for creating virtual connection devices")
    parser.add_argument(
        "-d",
        "--debug",
        help="Enable debug output",
        default=False,
        dest="debug",
        required=False,
        action="store_true",
    )
    parser.add_argument(
        "-b",
        "--broker",
        help="Set broker URL",
        default=DEFAULT_BROKER_URL,
        dest="broker",
        required=False,
    )
    options = parser.parse_args()

    if options.debug:
        logging_level = logging.DEBUG
    else:
        logging_level = logging.INFO

    logging.basicConfig(level=logging_level)

    connections_mediator = ConnectionsMediator(options.broker)

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
