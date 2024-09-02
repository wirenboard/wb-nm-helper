import argparse
import asyncio
import copy
import enum
import logging
import os
import signal
import struct
import sys
import threading
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass

import dbus
import dbus.lowlevel
import dbus.mainloop.glib
import dbus.types
from gi.repository import GLib
from wb_common.mqtt_client import DEFAULT_BROKER_URL, MQTTClient

from wb.nm_helper import wbmqtt
from wb.nm_helper.connection_checker import ConnectionChecker
from wb.nm_helper.connection_manager import DBUS_SERVICE_NAME, check_connectivity
from wb.nm_helper.network_manager import NMActiveConnection

CONNECTIVITY_CHECK_PERIOD = 20
MQTT_DRIVER_NAME = "wb-nm-helper"
MQTT_DEVICE_TOPIC_PREFIX = "system__networks__"
PERMANENT_CONNECTED_TYPES = ["loopback", "bridge", "tun"]


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

    def call_later(self, delay, callback):
        return self._event_loop.call_later(delay, callback)


class EventType(enum.Enum):
    COMMON_CREATE = enum.auto()
    COMMON_SWITCH = enum.auto()
    COMMON_REMOVE = enum.auto()

    ACTIVE_PROPERTIES_UPDATED = enum.auto()
    ACTIVE_CONNECTIVITY_UPDATED = enum.auto()
    ACTIVE_DEACTIVATED_BY_CM = enum.auto()

    ACTIVE_LIST_UPDATE = enum.auto()

    RELOAD = enum.auto()


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


class Mediator(ABC):  # pylint: disable=R0903
    @abstractmethod
    def new_event(self, event: Event):
        pass


@dataclass
class SubscriptionTarget:
    signal_name: str = None
    dbus_iface: str = None
    bus_name: str = None


class DbusSignalSubscription:
    def __init__(
        self,
        mediator: Mediator,
        dbus_bus: dbus.Bus,
        subscription_target: SubscriptionTarget,
        event_type: EventType,
    ):
        self._mediator = mediator
        self._bus = dbus_bus
        self._path = None
        self._handler_match = None
        self._additional_params = {}
        self._subscription_target = subscription_target
        self._event_type = event_type

    def subscribe(self, dbus_path: str, **kwargs):
        if self._path != dbus_path:
            self.unsubscribe()
            self._additional_params = kwargs
            self._path = dbus_path
            self._handler_match = self._bus.add_signal_receiver(
                self._signal_handler,
                self._subscription_target.signal_name,
                self._subscription_target.dbus_iface,
                self._subscription_target.bus_name,
                self._path,
            )

    def unsubscribe(self):
        if self._handler_match is not None:
            self._handler_match.remove()
            self._handler_match = None

    def _signal_handler(self, *args, **_):
        self._additional_params["new_properties"] = args[1]
        self._mediator.new_event(Event(self._event_type, **self._additional_params))


class ConnectionState(enum.Enum):
    UNKNOWN = 0
    ACTIVATING = 1
    ACTIVATED = 2
    DEACTIVATING = 3
    DEACTIVATED = 4


@dataclass
class MqttConnectionState:  # pylint: disable=R0902
    active: bool = False
    device: str = ""
    connection_state: ConnectionState = ConnectionState.DEACTIVATED
    address: str = ""
    connectivity: bool = False
    operator_name: str = None
    signal_quality: str = None
    access_tech: str = None


class ConnectionsMediator(Mediator):  # pylint: disable=R0902
    def __init__(self, mqtt_client) -> None:
        super().__init__()
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        dbus.mainloop.glib.threads_init()
        self._bus = dbus.SystemBus()
        self._dbus_loop = GLib.MainLoop()
        self._mqtt_client = mqtt_client

        self._common_connections = {}
        self._active_connections = {}
        self._event_loop = EventLoop()
        self._connectivity_updater = ConnectivityUpdater(self, self._bus)

        self._set_connections_event_handlers()
        self._deactivation_monitor = DeactivationMonitor(self)

    def run(self):
        self._event_loop.run()
        self._connectivity_updater.run()

        self._create_common_connections()
        self._create_active_connections()

        self._dbus_loop.run()

    def stop(self):
        self._event_loop.stop()
        self._connectivity_updater.stop()
        self._dbus_loop.quit()
        for connection in self._common_connections.values():
            connection.stop()

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

    def _common_connection_switch(self, connection_path: str):
        connection = self._common_connections.get(connection_path)
        if connection is None:
            return

        connection.set_updown_button_readonly(True)

        active_connections_path = [
            active_path
            for active_path, active_connection in self._active_connections.items()
            if active_connection.connection_path == connection_path
        ]

        if len(active_connections_path) == 0:
            logging.info("Activate connection: %s", connection_path)
            connection.activate()
        elif len(active_connections_path) == 1:
            logging.info("Deactivate connection: %s", active_connections_path[0])
            self._active_connections[active_connections_path[0]].deactivate()
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
                new_active_connection = ActiveConnection(
                    self, self._bus, new_active_path, self._connectivity_updater
                )
                new_active_connection.run()
                self._update_common_connection(
                    new_active_connection.connection_path, new_active_connection.state
                )
                self._active_connections[new_active_path] = new_active_connection
            except dbus.exceptions.DBusException:
                # When connection up/down/create/remove is in process, active connections list
                # changes very fast and it's impossible to create some temporary active connections
                # because they are removing faster than we can read their properties.
                # Finally, when some active connection becomes stable,
                # we can successfully read its properties on active connections list update signal.
                # So this message mostly for debug
                logging.debug("New active connection create failed %s", new_active_path)

        for old_active_path in old_active_paths:
            old_active_connection = self._active_connections[old_active_path]
            old_active_connection.stop()

            self._update_common_connection(old_active_connection.connection_path, old_active_connection.state)

            self._active_connections.pop(old_active_path)

    def _active_connection_connectivity_updated(self, active_connection_path: str, connectivity: bool):
        active_connection = self._active_connections.get(active_connection_path)
        if active_connection is not None:
            active_connection.update_connectivity(connectivity)
            self._update_common_connection(active_connection.connection_path, active_connection.state)

    # ActiveConnection have been updated by dbus
    def _active_connection_properties_updated(self, active_connection_path: str, properties):
        active_connection = self._active_connections.get(active_connection_path)
        if active_connection is not None:
            active_connection.update(properties)
            self._update_common_connection(active_connection.connection_path, active_connection.state)

    def _reload_connectivity(self):
        for active_connection in self._active_connections:
            self._connectivity_updater.update(active_connection, CONNECTIVITY_CHECK_PERIOD)

    def _update_common_connection(self, connection_path: str, state: MqttConnectionState) -> None:
        connection = self._common_connections.get(connection_path)
        if connection is not None:
            connection.update(state)

    def _active_connection_deactivated_by_cm(self, active_connection_path: str) -> None:
        active_connection = self._active_connections.get(active_connection_path)
        if active_connection is None:
            return

        connection = self._common_connections.get(active_connection.connection_path)
        if connection is not None:
            connection.set_deactivated_by_cm()

    async def _run_async_event(self, event: Event):
        logging.debug("Execute event %s %s %s", event.number, event.type.name, event.kwargs)
        try:
            if event.type == EventType.COMMON_CREATE:
                self._common_connection_create(event.kwargs.get("path"))

            elif event.type == EventType.COMMON_SWITCH:
                self._common_connection_switch(event.kwargs.get("connection_path"))

            elif event.type == EventType.COMMON_REMOVE:
                self._common_connection_remove(event.kwargs.get("path"))

            elif event.type == EventType.ACTIVE_LIST_UPDATE:
                self._active_connections_list_update(event.kwargs.get("path_list"))

            elif event.type == EventType.ACTIVE_CONNECTIVITY_UPDATED:
                self._active_connection_connectivity_updated(
                    event.kwargs.get("active_connection_path"), event.kwargs.get("connectivity")
                )

            elif event.type == EventType.ACTIVE_PROPERTIES_UPDATED:
                self._active_connection_properties_updated(
                    event.kwargs.get("active_connection_path"), event.kwargs.get("new_properties")
                )

            elif event.type == EventType.RELOAD:
                self._reload_connectivity()

            elif event.type == EventType.ACTIVE_DEACTIVATED_BY_CM:
                self._active_connection_deactivated_by_cm(event.kwargs.get("active_connection_path"))

        except BaseException as ex:
            logging.error(
                "Error during event execution %s",
                "\n".join(
                    [
                        "".join(traceback.format_exception_only(None, ex)).strip(),
                        "".join(traceback.format_exception(None, ex, ex.__traceback__)).strip(),
                    ]
                ),
            )
            raise

    def new_event(self, event: Event):
        self._event_loop.run_coroutine_threadsafe(self._run_async_event(event))


class ConnectivityUpdater:
    def __init__(self, mediator: Mediator, bus: dbus.Bus):
        self._mediator = mediator
        self._bus = bus
        self._event_loop = EventLoop()
        self._futures = {}
        self._connection_checker = ConnectionChecker()

    def run(self):
        self._event_loop.run()

    def stop(self):
        self._event_loop.stop()

    # update connectivity for active connection now
    def update(self, active_connection_path: str, period=None):
        # don't check connectivity if connection has permanent connectivity
        if self._has_permanent_connectivity(active_connection_path):
            self._mediator.new_event(
                Event(
                    EventType.ACTIVE_CONNECTIVITY_UPDATED,
                    active_connection_path=active_connection_path,
                    connectivity=True,
                )
            )
            return

        # cancel previous connectivity check
        if active_connection_path in self._futures:
            self._futures[active_connection_path].cancel()

        # run connectivity check in ConnectivityUpdater event loop
        self._futures[active_connection_path] = self._event_loop.run_coroutine_threadsafe(
            self._check_connectivity(active_connection_path, period)
        )

    #  assume that some connections always have a positive connectivity based on their settings
    def _has_permanent_connectivity(self, active_connection_path: str) -> bool:
        active_connection = NMActiveConnection(active_connection_path, self._bus)
        try:
            settings = active_connection.get_connection().get_settings()
            if settings["connection"]["type"] in PERMANENT_CONNECTED_TYPES:
                return True
            if settings.get("802-11-wireless", {}).get("mode") == "ap":
                return True
            return settings.get("user", {}).get("data", {}).get("wb.read-only", "false") == "true"
        except dbus.exceptions.DBusException as ex:
            logging.debug("Can't define if connection has permanent connectivity: %s", ex)
            return False

    async def _check_connectivity(self, active_connection_path: str, period):
        logging.debug("Check connectivity for %s", active_connection_path)

        while True:
            connectivity = False
            try:
                nm_active_connection = NMActiveConnection(active_connection_path, self._bus)
                connectivity = check_connectivity(nm_active_connection, self._connection_checker)
            except BaseException as ex:  # pylint: disable=W0718
                logging.error("Unable to read connectivity for %s: %s", active_connection_path, ex)

            self._mediator.new_event(
                Event(
                    EventType.ACTIVE_CONNECTIVITY_UPDATED,
                    active_connection_path=active_connection_path,
                    connectivity=connectivity,
                )
            )
            if period:
                await asyncio.sleep(period)
            else:
                break

    def stop_updates(self, active_connection_path: str):
        if active_connection_path in self._futures:
            self._futures[active_connection_path].cancel()


class CommonConnection:  # pylint: disable=R0902
    NAME_CONTROL_META = wbmqtt.ControlMeta(
        control_type="text",
        order=1,
        read_only=True,
    )
    UUID_CONTROL_META = wbmqtt.ControlMeta(
        control_type="text",
        order=2,
        read_only=True,
    )
    TYPE_CONTROL_META = wbmqtt.ControlMeta(
        control_type="text",
        order=3,
        read_only=True,
    )
    ACTIVE_CONTROL_META = wbmqtt.ControlMeta(
        control_type="switch",
        order=4,
        read_only=True,
    )
    DEVICE_CONTROL_META = wbmqtt.ControlMeta(
        control_type="text",
        order=5,
        read_only=True,
    )
    STATE_CONTROL_META = wbmqtt.ControlMeta(
        control_type="text",
        order=6,
        read_only=True,
    )
    ADDRESS_CONTROL_META = wbmqtt.ControlMeta(
        control_type="text",
        order=7,
        read_only=True,
    )
    CONNECTIVITY_CONTROL_META = wbmqtt.ControlMeta(
        control_type="switch",
        order=8,
        read_only=True,
    )
    OPERATOR_CONTROL_META = wbmqtt.ControlMeta(
        control_type="text",
        order=9,
        read_only=True,
    )
    SIGNAL_QUALITY_CONTROL_META = wbmqtt.ControlMeta(
        title="Signal Quality",
        control_type="text",
        order=10,
        read_only=True,
    )
    ACCESS_TECH_CONTROL_META = wbmqtt.ControlMeta(
        title="Access Technologies",
        control_type="text",
        order=11,
        read_only=True,
    )
    UPDOWN_CONTROL_META = wbmqtt.ControlMeta(
        title="Up",
        control_type="pushbutton",
        order=12,
        read_only=False,
    )

    def __init__(
        self,
        mediator: Mediator,
        mqtt_client: MQTTClient,
        dbus_bus: dbus.Bus,
        dbus_path: str,
    ):
        self._mediator = mediator
        self._bus = dbus_bus
        self.dbus_path = dbus_path
        self._mqtt_client = mqtt_client
        self._type = None
        self._name = None
        self._uuid = None
        self._mqtt_device = None
        self._deactivated_by_cm = False

        logging.info("New connection %s", self.dbus_path)

    def run(self):
        self._read_dbus_settings()
        self._create_virtual_device()

    def set_updown_button_readonly(self, read_only: bool) -> None:
        self._mqtt_device.set_control_read_only("UpDown", read_only)

    def update(self, state: MqttConnectionState) -> None:
        self._mqtt_device.set_control_value("Active", "1" if state.active else "0")
        self._mqtt_device.set_control_title("UpDown", "Down" if state.active else "Up")
        self._mqtt_device.set_control_value("Device", state.device)

        if state.connection_state in (ConnectionState.ACTIVATED, ConnectionState.ACTIVATING):
            self._deactivated_by_cm = False
        state_name = state.connection_state.name.lower()
        if self._deactivated_by_cm and state.connection_state in (
            ConnectionState.DEACTIVATED,
            ConnectionState.DEACTIVATING,
        ):
            state_name = state_name + " by wb-connection-manager"
        self._mqtt_device.set_control_value("State", state_name)

        self._mqtt_device.set_control_value("Address", state.address)
        self._mqtt_device.set_control_value("Connectivity", "1" if state.connectivity else "0")
        if self._type == "gsm":
            self._mqtt_device.set_control_value("Operator", state.operator_name)
            self._mqtt_device.set_control_value("SignalQuality", state.signal_quality)
            self._mqtt_device.set_control_value("AccessTechnologies", state.access_tech)
        logging.debug(
            "Update virtual device settings for %s %s %s %s", self._name, self._uuid, self.dbus_path, state
        )

    def set_deactivated_by_cm(self) -> None:
        if self._type == "gsm":
            self._deactivated_by_cm = True

    def stop(self):
        self._remove_virtual_device()

    def activate(self):
        try:
            proxy = self._bus.get_object("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager")
            interface = dbus.Interface(proxy, "org.freedesktop.NetworkManager")
            empty_proxy = self._bus.get_object("org.freedesktop.NetworkManager", "/")
            # ActivateConnection and DeactivateConnection functions ends very fast
            # even if connection activating/deactivating process can take a long time
            interface.ActivateConnection(self.dbus_path, empty_proxy, empty_proxy)
        except dbus.exceptions.DBusException:
            logging.error(
                "Unable to activate %s %s connection, no suitable device found",
                self._name,
                self._uuid,
            )
            # this is for interface
            self._mqtt_device.set_control_value("State", "deactivated", force=True)

    def _read_dbus_settings(self):
        proxy = self._bus.get_object("org.freedesktop.NetworkManager", self.dbus_path)
        interface = dbus.Interface(proxy, "org.freedesktop.NetworkManager.Settings.Connection")
        dbus_settings = interface.GetSettings()
        self._name = str(dbus_settings["connection"]["id"])
        self._uuid = str(dbus_settings["connection"]["uuid"])
        self._type = str(dbus_settings["connection"]["type"])

    def _updown_message_callback(self, _, __, ___):
        self._mediator.new_event(Event(EventType.COMMON_SWITCH, connection_path=self.dbus_path))

    def _create_virtual_device(self):
        self._mqtt_device = wbmqtt.Device(
            self._mqtt_client,
            MQTT_DEVICE_TOPIC_PREFIX + self._uuid,
            "Network Connection " + self._name,
            MQTT_DRIVER_NAME,
        )

        active_value = "1" if MqttConnectionState.active else "0"
        state_value = MqttConnectionState.connection_state.name.lower()
        connectivity_value = "1" if MqttConnectionState.connectivity else "0"

        self._mqtt_device.create_control("Name", self.NAME_CONTROL_META, self._name)
        self._mqtt_device.create_control("UUID", self.UUID_CONTROL_META, self._uuid)
        self._mqtt_device.create_control("Type", self.TYPE_CONTROL_META, self._type)
        self._mqtt_device.create_control("Active", self.ACTIVE_CONTROL_META, active_value)
        self._mqtt_device.create_control("Device", self.DEVICE_CONTROL_META, MqttConnectionState.device)
        self._mqtt_device.create_control("State", self.STATE_CONTROL_META, state_value)
        self._mqtt_device.create_control("Address", self.ADDRESS_CONTROL_META, MqttConnectionState.address)
        self._mqtt_device.create_control("Connectivity", self.CONNECTIVITY_CONTROL_META, connectivity_value)
        self._mqtt_device.create_control("UpDown", self.UPDOWN_CONTROL_META, None)  # button
        self._mqtt_device.add_control_message_callback("UpDown", self._updown_message_callback)
        if self._type == "gsm":
            self._mqtt_device.create_control(
                "Operator", self.OPERATOR_CONTROL_META, MqttConnectionState.operator_name
            )
            self._mqtt_device.create_control(
                "SignalQuality", self.SIGNAL_QUALITY_CONTROL_META, MqttConnectionState.signal_quality
            )
            self._mqtt_device.create_control(
                "AccessTechnologies", self.ACCESS_TECH_CONTROL_META, MqttConnectionState.access_tech
            )

        logging.info("New virtual device %s %s %s", self._name, self._uuid, self.dbus_path)

    def _remove_virtual_device(self):
        if self._mqtt_device is not None:
            self._mqtt_device.remove_device()
        logging.info("Remove virtual device %s %s %s", self._name, self._uuid, self.dbus_path)


class DeactivationMonitor:
    def __init__(self, mediator: Mediator) -> None:
        self._mediator = mediator
        self._private_bus = dbus.SystemBus(private=True)

        obj_dbus = self._private_bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
        iface = dbus.Interface(obj_dbus, "org.freedesktop.DBus.Monitoring")
        iface.BecomeMonitor(
            [
                "member=DeactivateConnection,sender=" + DBUS_SERVICE_NAME,
            ],
            dbus.UInt32(0),
        )

        self._private_bus.add_message_filter(self)

    def __call__(self, _bus, msg: dbus.lowlevel.Message) -> None:
        args = msg.get_args_list()
        if args[0].startswith("/org/freedesktop/NetworkManager/ActiveConnection"):
            logging.debug("Connection deactivation from %s\n%s", msg.get_sender(), args)
            self._mediator.new_event(
                Event(EventType.ACTIVE_DEACTIVATED_BY_CM, active_connection_path=args[0])
            )

    def stop(self) -> None:
        self._private_bus.close()


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


class ActiveConnection:  # pylint: disable=R0902
    def __init__(
        self,
        mediator: Mediator,
        dbus_bus: dbus.Bus,
        dbus_path: str,
        connectivity_updater: ConnectivityUpdater,
    ):
        self._mediator = mediator
        self._bus = dbus_bus
        self._path = dbus_path
        self._connectivity_updater = connectivity_updater

        self._active_connection_properties_changed_subscription = (
            self._create_subscription_on_dbus_properties()
        )
        self._ipv4_config_changed_subscription = self._create_subscription_on_dbus_properties()

        self.connection_path = None
        self.state = MqttConnectionState(active=True)
        self._type = None
        self._name = None
        self._uuid = None
        self._modem_path = None

    def run(self):
        dbus_properties = self._read_connection_dbus_properties(self._path)
        self.update(dbus_properties)

        self._active_connection_properties_changed_subscription.subscribe(
            self._path, active_connection_path=self._path
        )

        logging.info(
            "New active connection %s %s %s %s",
            self._name,
            self._uuid,
            self.connection_path,
            self._path,
        )

    def _read_connection_dbus_properties(self, path) -> dict:
        try:
            proxy = self._bus.get_object("org.freedesktop.NetworkManager", path)
            interface = dbus.Interface(proxy, "org.freedesktop.DBus.Properties")
            return interface.GetAll("org.freedesktop.NetworkManager.Connection.Active")
        except dbus.exceptions.DBusException:
            # Please read message about ActiveConnection creation process in
            # active connections list update event handler
            logging.debug(
                "Read active connection %s %s properties failed",
                self.connection_path,
                path,
            )
            raise

    def _read_modem_dbus_properties(self, modem_path) -> dict:
        try:
            proxy = self._bus.get_object("org.freedesktop.ModemManager1", modem_path)
            interface = dbus.Interface(proxy, "org.freedesktop.ModemManager1.Modem.Simple")
            return interface.GetStatus()
        except dbus.exceptions.DBusException:
            logging.debug("Read modem %s properties failed", modem_path)
            return {}

    def _read_ipv4_dbus_properties(self, ip4config_path) -> dict:
        try:
            proxy = self._bus.get_object("org.freedesktop.NetworkManager", ip4config_path)
            interface = dbus.Interface(proxy, "org.freedesktop.DBus.Properties")
            addresses = interface.Get("org.freedesktop.NetworkManager.IP4Config", "Addresses")
            return {"Addresses": addresses}
        except dbus.exceptions.DBusException:
            logging.debug("Error reading Ip4Config properties %s", ip4config_path)
            return {}

    def _read_device_dbus_properties(self, device_path) -> dict:
        try:
            device_proxy = self._bus.get_object("org.freedesktop.NetworkManager", device_path)
            device_interface = dbus.Interface(device_proxy, "org.freedesktop.DBus.Properties")
            interface = device_interface.Get("org.freedesktop.NetworkManager.Device", "Interface")
            return {"Interface": interface}
        except dbus.exceptions.DBusException:
            logging.debug("Error reading device properties %s", device_path)
            return {}

    def _find_modem_dbus_path_by_device(self, device):
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

    def update(self, dbus_properties):
        logging.debug("Update active connection %s: %s", self._path, dbus_properties)

        old_state = copy.deepcopy(self.state)
        self._update_connection_info_from_dbus_properties(dbus_properties)
        self._update_state_from_dbus_properties(dbus_properties)
        new_state = self.state

        if (  # update modem info for gsm connections when device is changed
            new_state.device != old_state.device and self._type == "gsm"
        ):
            if new_state.device != MqttConnectionState.device:
                self._modem_path = self._find_modem_dbus_path_by_device(new_state.device)
            else:
                self._modem_path = None

        if (  # update operator, signal, technology for gsm connections if state is changed
            new_state.connection_state != old_state.connection_state
            and self._modem_path is not None
            and self._type == "gsm"
        ):
            self._update_state_from_modem_properties(self._modem_path)

        if (  # schedule connectivity check if connection was activated
            old_state.connection_state != ConnectionState.ACTIVATED
            and new_state.connection_state == ConnectionState.ACTIVATED
        ):
            self._connectivity_updater.update(self._path, CONNECTIVITY_CHECK_PERIOD)

        if (  # stop scheduled check if connection was deactivated
            old_state.connection_state == ConnectionState.ACTIVATED
            and new_state.connection_state != ConnectionState.ACTIVATED
        ):
            self._connectivity_updater.stop_updates(self._path)

        self._update_subscriptions_from_dbus_properties(dbus_properties)

    def update_connectivity(self, connectivity: bool):
        self.state.connectivity = connectivity

    # update basic information about connection
    def _update_connection_info_from_dbus_properties(self, dbus_properties):
        if "Id" in dbus_properties:
            self._name = dbus_properties["Id"]
        if "Uuid" in dbus_properties:
            self._uuid = dbus_properties["Uuid"]
        if "Type" in dbus_properties:
            self._type = dbus_properties["Type"]
        if "Connection" in dbus_properties:
            self.connection_path = dbus_properties["Connection"]

    # update connection_state, address, device
    def _update_state_from_dbus_properties(self, dbus_properties):
        # pylint: disable=too-many-branches

        if "State" in dbus_properties:
            self.state.connection_state = ConnectionState(dbus_properties["State"])

        if "Ip4Config" in dbus_properties:
            if dbus_properties["Ip4Config"] == "/":
                self.state.address = MqttConnectionState.address
            else:
                ipv4_properties = self._read_ipv4_dbus_properties(dbus_properties["Ip4Config"])
                if "Addresses" in ipv4_properties:
                    self.state.address = self._format_ip4address_list(ipv4_properties["Addresses"])

        if "Devices" in dbus_properties:
            if len(dbus_properties["Devices"]) == 0:
                self.state.device = MqttConnectionState.device
            else:
                device_properties = self._read_device_dbus_properties(dbus_properties["Devices"][0])
                if "Interface" in device_properties:
                    self.state.device = device_properties["Interface"]

        # on ipv4_config_changed_subscription
        if "Addresses" in dbus_properties:
            self.state.address = self._format_ip4address_list(dbus_properties["Addresses"])

    def _format_ip4address_list(self, ip4addresses_list):
        ip4addresses = []
        for ip4address in ip4addresses_list:
            ip4addresses.append(
                ".".join([str(x) for x in struct.unpack("<BBBB", struct.pack("<I", ip4address[0]))])
            )
        unical_ip4addresses = list(set(ip4addresses))

        return " ".join(unical_ip4addresses)

    # update signal quality, operator name, access technologies
    def _update_state_from_modem_properties(self, modem_path):
        modem_properties = self._read_modem_dbus_properties(modem_path)
        if "access-technologies" in modem_properties:
            access_tech = ModemAccessTechnology(modem_properties["access-technologies"])
            self.state.access_tech = access_tech.name.replace("MM_MODEM_ACCESS_TECHNOLOGY_", "").upper()
        if "signal-quality" in modem_properties:
            self.state.signal_quality = modem_properties["signal-quality"][0]
        if "m3gpp-operator-name" in modem_properties:
            self.state.operator_name = modem_properties["m3gpp-operator-name"]

    # create subscription on active connection and ip v4 config dbus properties
    def _create_subscription_on_dbus_properties(self) -> DbusSignalSubscription:
        return DbusSignalSubscription(
            self._mediator,
            self._bus,
            SubscriptionTarget(
                "PropertiesChanged",
                "org.freedesktop.DBus.Properties",
                "org.freedesktop.NetworkManager",
            ),
            EventType.ACTIVE_PROPERTIES_UPDATED,
        )

    def _update_subscriptions_from_dbus_properties(self, dbus_properties):
        if "Ip4Config" in dbus_properties:
            if dbus_properties["Ip4Config"] == "/":
                self._ipv4_config_changed_subscription.unsubscribe()
            else:
                self._ipv4_config_changed_subscription.subscribe(
                    dbus_properties["Ip4Config"], active_connection_path=self._path
                )

    # deactivate active connection via dbus
    def deactivate(self) -> None:
        try:
            proxy = self._bus.get_object("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager")
            interface = dbus.Interface(proxy, "org.freedesktop.NetworkManager")
            # ActivateConnection and DeactivateConnection functions ends very fast
            # even if connection activating/deactivating process can take a long time
            interface.DeactivateConnection(self._path)
        except dbus.exceptions.DBusException:
            logging.error("The connection %s was not active", self._path)

    # clear subscriptions and state before removing object (connnection deactivated)
    def stop(self):

        self._active_connection_properties_changed_subscription.unsubscribe()
        self._ipv4_config_changed_subscription.unsubscribe()

        self._connectivity_updater.stop_updates(self._path)

        # set default state
        self.state = MqttConnectionState()

        logging.info("Remove active connection %s %s", self.connection_path, self._path)


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
    parser.add_argument(
        "-r",
        "--reload",
        help="Reload main process of this service",
        dest="main_process_pid",
        default=0,
        required=False,
    )
    options = parser.parse_args()

    if options.debug:
        logging_level = logging.DEBUG
    else:
        logging_level = logging.INFO

    logging.basicConfig(level=logging_level)

    if options.main_process_pid:
        pid_fd = os.pidfd_open(int(options.main_process_pid), 0)
        signal.pidfd_send_signal(pid_fd, signal.SIGHUP)
        logging.info("Send SIGHUP signal to %s process", options.main_process_pid)
        return

    mqtt_client = MQTTClient("connections-virtual-devices", options.broker)
    mqtt_client.start()

    wbmqtt.remove_topics_by_device_prefix(mqtt_client, MQTT_DEVICE_TOPIC_PREFIX)

    connections_mediator = ConnectionsMediator(mqtt_client)

    def stop_virtual_connections_client(_, __):
        connections_mediator.stop()
        mqtt_client.stop()

    def reload_virtual_connections_client(_, __):
        connections_mediator.new_event(Event(EventType.RELOAD))

    signal.signal(signal.SIGINT, stop_virtual_connections_client)
    signal.signal(signal.SIGTERM, stop_virtual_connections_client)
    signal.signal(signal.SIGHUP, reload_virtual_connections_client)

    try:
        connections_mediator.run()
    except (KeyboardInterrupt, dbus.exceptions.DBusException):
        pass
    finally:
        logging.info("Stopping")
        connections_mediator.stop()
        mqtt_client.stop()


if __name__ == "__main__":
    sys.exit(main())
