# pylint: disable=too-many-instance-attributes disable=consider-using-with
# pylint: disable=no-member disable=protected-access
import subprocess
import time
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
from wb_common.mqtt_client import MQTTClient

import wb.nm_helper.virtual_devices

from . import connections_settings as connections
from . import mqtt_publications as publications


class MemoryManager(BaseManager):
    pass


class MQTTNetworkManagerTest(dbusmock.DBusTestCase):
    def setUp(self):
        self.start_system_bus()
        self.system_bus = self.get_dbus(system_bus=True)

        (self.io_mock, self.obj_networkmanager) = self.spawn_server_template(
            "networkmanager",
            {"NetworkingEnabled": True},
            stdout=subprocess.PIPE,
        )
        self.networkmanager_mock = dbus.Interface(self.obj_networkmanager, dbusmock.MOCK_IFACE)
        self.networkmanager = dbus.Interface(
            self.system_bus.get_object(MANAGER_IFACE, MANAGER_OBJ), MANAGER_IFACE
        )
        self.settings = dbus.Interface(
            self.system_bus.get_object(MANAGER_IFACE, SETTINGS_OBJ), SETTINGS_IFACE
        )

        self.connections = {}
        self.connections.update({"eth0": self.settings.AddConnection(connections.ETH0_DBUS_SETTINGS)})
        self.connections.update({"eth1": self.settings.AddConnection(connections.ETH1_DBUS_SETTINGS)})

        self.networkmanager_mock.AddEthernetDevice("mock_eth0", "eth0", DeviceState.ACTIVATED)

        self.networkmanager.ActivateConnection(self.connections["eth0"], "/", "/")

        self.mediator = None

        MemoryManager.register("list", list)
        self.manager = MemoryManager()
        self.manager.start()

        self.mqtt_publications = self.manager.list()

        self.proc = Process(target=self.start_mediator)
        self.proc.start()

    def start_mediator(self):
        mqtt_mock = Mock(MQTTClient)
        mqtt_mock.publish.side_effect = self.publish

        self.mediator = wb.nm_helper.virtual_devices.ConnectionsMediator(mqtt_mock)
        self.mediator.run()

    def publish(self, topic, value, _retain):
        self.mqtt_publications.append((topic, value))

    def tearDown(self):
        self.proc.kill()
        if self.io_mock:
            self.io_mock.stdout.close()
            self.io_mock.terminate()
            self.io_mock.wait()
            self.io_mock = None

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
