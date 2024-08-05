# from dbusmock.pytest_fixtures import dbusmock_system
import subprocess
import time
import unittest
from multiprocessing import Process
from multiprocessing.managers import BaseManager
from unittest.mock import Mock

import dbus
import dbus.mainloop.glib
import dbusmock
from dbusmock.templates.networkmanager import (
    CSETTINGS_IFACE,
    MANAGER_IFACE,
    MANAGER_OBJ,
    SETTINGS_IFACE,
    SETTINGS_OBJ,
    DeviceState,
)
from gi.repository import GLib
from wb_common.mqtt_client import MQTTClient

import wb.nm_helper.virtual_devices

from . import mqtt_publications as publications

ETH0_DBUS_SETTINGS = dbus.Dictionary(
    {
        "connection": dbus.Dictionary(
            {
                "id": dbus.String("wb-eth0", variant_level=1),
                "interface-name": dbus.String("eth0", variant_level=1),
                "type": "802-3-ethernet",
                "uuid": dbus.String("91f1c71d-2d97-4675-886f-ecbe52b8451e", variant_level=1),
            },
            signature=dbus.Signature("sv"),
        ),
        "ipv4": dbus.Dictionary(
            {"method": dbus.String("auto", variant_level=1)},
            signature=dbus.Signature("sv"),
        ),
    },
    signature=dbus.Signature("sa{sv}"),
)

ETH1_DBUS_SETTINGS = dbus.Dictionary(
    {
        "connection": dbus.Dictionary(
            {
                "id": dbus.String("wb-eth1", variant_level=1),
                "interface-name": dbus.String("eth1", variant_level=1),
                "type": "802-3-ethernet",
                "uuid": dbus.String("c3e38405-9c17-4155-ad70-664311b49066", variant_level=1),
            },
            signature=dbus.Signature("sv"),
        ),
        "ipv4": dbus.Dictionary(
            {"method": dbus.String("auto", variant_level=1)},
            signature=dbus.Signature("sv"),
        ),
    },
    signature=dbus.Signature("sa{sv}"),
)


class MemoryManager(BaseManager):
    pass


class MQTTNetworkManagerTest(dbusmock.DBusTestCase):

    def setUp(self):
        self.start_system_bus()
        self.system_bus = self.get_dbus(system_bus=True)

        (self.p_mock, self.obj_networkmanager) = self.spawn_server_template(
            "networkmanager",
            {"NetworkingEnabled": True},
            stdout=subprocess.PIPE,
            system_bus=True,
        )
        self.networkmanager_mock = dbus.Interface(self.obj_networkmanager, dbusmock.MOCK_IFACE)
        self.networkmanager = dbus.Interface(
            self.system_bus.get_object(MANAGER_IFACE, MANAGER_OBJ), MANAGER_IFACE
        )
        self.settings = dbus.Interface(
            self.system_bus.get_object(MANAGER_IFACE, SETTINGS_OBJ), SETTINGS_IFACE
        )

        self.connections = {}
        self.connections.update({"eth0": self.settings.AddConnection(ETH0_DBUS_SETTINGS)})
        self.connections.update({"eth1": self.settings.AddConnection(ETH1_DBUS_SETTINGS)})

        self.devices = {}
        self.devices.update(
            {"eth0": self.networkmanager_mock.AddEthernetDevice("mock_eth0", "eth0", DeviceState.ACTIVATED)}
        )

        self.networkmanager.ActivateConnection(self.connections["eth0"], "/", "/")

        MemoryManager.register("list", list)
        self.manager = MemoryManager()
        self.manager.start()

        self.mqtt_publications = self.manager.list()

        self.proc = Process(target=self.start_mediator)
        self.proc.start()

    def start_mediator(self):
        self.mqtt_mock = Mock(MQTTClient)
        self.mqtt_mock.publish.side_effect = self.publish

        self.mediator = wb.nm_helper.virtual_devices.ConnectionsMediator(self.mqtt_mock)
        self.mediator.run()

    def publish(self, topic, value, retain):
        self.mqtt_publications.append((topic, value))
        print(f"('{topic}','{value}'),")

    def tearDown(self):
        self.proc.kill()
        if self.p_mock:
            self.p_mock.stdout.close()
            self.p_mock.terminate()
            self.p_mock.wait()
            self.p_mock = None

    def wait_for(self, condition, timeout, poll_interval=0):
        current_time = time.time()
        while not condition():
            if (time.time() - current_time) > timeout:
                return False
            time.sleep(poll_interval)
        return True

    def test_main(self):
        self.assert_connections_init()
        self.assert_delete_active_connection()
        self.assert_delete_non_active_connection()

    # check that the connection devices that were added before starting the service have been created
    def assert_connections_init(self):
        self.wait_for(
            lambda: len(self.mqtt_publications._getvalue())
            == len(publications.CONNECTIONS_INIT_PUBLICATIONS),
            3,
            1,
        )
        assert self.mqtt_publications._getvalue() == publications.CONNECTIONS_INIT_PUBLICATIONS

    def assert_delete_active_connection(self):
        self.mqtt_publications.clear()
        connection = dbus.Interface(
            self.system_bus.get_object(MANAGER_IFACE, self.connections["eth0"]), CSETTINGS_IFACE
        )
        connection.Delete()
        self.connections.pop("eth0")
        self.wait_for(
            lambda: len(self.mqtt_publications._getvalue())
            == len(publications.ACTIVE_CONNECTION_DELETE_PUBLICATIONS),
            3,
            1,
        )
        assert self.mqtt_publications._getvalue() == publications.ACTIVE_CONNECTION_DELETE_PUBLICATIONS

    def assert_delete_non_active_connection(self):
        self.mqtt_publications.clear()
        connection = dbus.Interface(
            self.system_bus.get_object(MANAGER_IFACE, self.connections["eth1"]), CSETTINGS_IFACE
        )
        connection.Delete()
        self.connections.pop("eth1")
        self.wait_for(
            lambda: len(self.mqtt_publications._getvalue())
            == len(publications.NON_ACTIVE_CONNECTION_DELETE_PUBLICATIONS),
            3,
            1,
        )
        assert self.mqtt_publications._getvalue() == publications.NON_ACTIVE_CONNECTION_DELETE_PUBLICATIONS
