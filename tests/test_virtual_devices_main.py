"""
Entry point of wb-mqtt-nm-helper before any virtual device exists: waiting for the broker, a
rejected login and a stop during the wait. The MQTT client is a mock, D-Bus is not touched and
the signal handlers are recorded instead of installed, so no real signal reaches pytest.
"""

import signal
import threading
import unittest
from unittest.mock import MagicMock, patch

from wb.nm_helper import virtual_devices


class VirtualDevicesMainTest(unittest.TestCase):
    def setUp(self):
        self.mqtt_client = MagicMock()
        # like wb-common's: the fake never connects, the wait ends only with the stop event
        self.mqtt_client.wait_for_connection.side_effect = lambda stop: not stop.wait(5)
        self.handlers = {}  # {signum: handler} main() asked signal.signal to install
        patches = [
            patch.object(virtual_devices, "MQTTClient", return_value=self.mqtt_client),
            patch.object(virtual_devices.sys, "argv", ["wb-mqtt-nm-helper"]),
            patch.object(virtual_devices.signal, "signal", side_effect=self.handlers.__setitem__),
            # main() opens the system bus before the MQTT client: keep it away from any real D-Bus
            patch.object(virtual_devices.dbus, "SystemBus"),
            patch.object(virtual_devices.dbus.mainloop.glib, "DBusGMainLoop"),
            patch.object(virtual_devices.dbus.mainloop.glib, "threads_init"),
        ]
        for one in patches:
            one.start()
            self.addCleanup(one.stop)

    def test_rejected_login_exits_with_2_before_publishing(self):
        """CONNACK 5 from paho's thread: the wait ends, nothing is published, exit code 2."""
        self.mqtt_client.start.side_effect = lambda **_: self.mqtt_client.on_connect(None, None, None, 5)

        self.assertEqual(virtual_devices.main(), virtual_devices.EXIT_INVALIDARGUMENT)

        self.mqtt_client.start.assert_called_once_with(retry_first_connection=True)
        self.mqtt_client.publish.assert_not_called()
        self.mqtt_client.stop.assert_called_once_with()

    def test_sigterm_while_waiting_for_the_broker_exits_with_0(self):
        """
        The broker never answers (paho keeps retrying); the recorded SIGTERM handler is called from a
        timer, as the interpreter would call it on the real signal, and ends the wait with exit code 0.
        """
        timer = threading.Timer(0.2, lambda: self.handlers[signal.SIGTERM](signal.SIGTERM, None))
        self.addCleanup(timer.cancel)
        timer.start()

        self.assertEqual(virtual_devices.main(), virtual_devices.EXIT_SUCCESS)

        self.mqtt_client.publish.assert_not_called()
        self.mqtt_client.stop.assert_called_once_with()

    def test_dbus_error_on_start_exits_with_1(self):
        """A D-Bus failure at startup must be a failure for systemd's Restart=on-failure."""
        self.mqtt_client.wait_for_connection.side_effect = None
        self.mqtt_client.wait_for_connection.return_value = True
        mediator = MagicMock()
        mediator.run.side_effect = virtual_devices.dbus.exceptions.DBusException("NoReply")

        with patch.object(virtual_devices, "ConnectionsMediator", return_value=mediator), patch.object(
            virtual_devices.wbmqtt, "remove_topics_by_device_prefix"
        ):
            self.assertEqual(virtual_devices.main(), virtual_devices.EXIT_FAILURE)

        mediator.stop.assert_called_once_with()
        self.mqtt_client.stop.assert_called_once_with()

    def test_dbus_error_while_creating_the_mediator_exits_with_1(self):
        self.mqtt_client.wait_for_connection.side_effect = None
        self.mqtt_client.wait_for_connection.return_value = True
        error = virtual_devices.dbus.exceptions.DBusException("AccessDenied")

        with patch.object(virtual_devices, "ConnectionsMediator", side_effect=error), patch.object(
            virtual_devices.wbmqtt, "remove_topics_by_device_prefix"
        ):
            self.assertEqual(virtual_devices.main(), virtual_devices.EXIT_FAILURE)

        self.mqtt_client.stop.assert_called_once_with()

    def test_failed_mediator_exits_with_1(self):
        """Connections that can't be read even after retries: exit with an error so systemd restarts it."""
        self.mqtt_client.wait_for_connection.side_effect = None
        self.mqtt_client.wait_for_connection.return_value = True
        mediator = MagicMock(failed=True)

        with patch.object(virtual_devices, "ConnectionsMediator", return_value=mediator), patch.object(
            virtual_devices.wbmqtt, "remove_topics_by_device_prefix"
        ):
            self.assertEqual(virtual_devices.main(), virtual_devices.EXIT_FAILURE)

        mediator.stop.assert_called_once_with()
