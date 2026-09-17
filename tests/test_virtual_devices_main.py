# pylint: disable=protected-access
"""
Entry point of wb-mqtt-nm-helper before any virtual device exists: waiting for the broker, a
rejected login and a stop during the wait. The MQTT client is a mock, D-Bus is not touched.
"""

import os
import signal
import threading
import unittest
from unittest.mock import MagicMock, patch

from wb.nm_helper import virtual_devices


class VirtualDevicesMainTest(unittest.TestCase):
    def setUp(self):
        self.mqtt_client = MagicMock()
        patches = [
            patch.object(virtual_devices, "MQTTClient", return_value=self.mqtt_client),
            patch.object(virtual_devices.sys, "argv", ["wb-mqtt-nm-helper"]),
        ]
        for one in patches:
            one.start()
            self.addCleanup(one.stop)
        self.saved_handlers = {
            sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        }

    def tearDown(self):
        for sig, handler in self.saved_handlers.items():
            signal.signal(sig, handler)

    def test_rejected_login_exits_with_2_before_publishing(self):
        """CONNACK 5 from paho's thread: the wait ends, nothing is published, exit code 2."""
        self.mqtt_client.start.side_effect = lambda **_: self.mqtt_client.on_connect(None, None, None, 5)

        self.assertEqual(virtual_devices.main(), virtual_devices.EXIT_INVALIDARGUMENT)

        self.mqtt_client.start.assert_called_once_with(retry_first_connection=True)
        self.mqtt_client.publish.assert_not_called()
        self.mqtt_client.stop.assert_called_once_with()

    def test_sigterm_while_waiting_for_the_broker_exits_with_0(self):
        """The broker never answers (paho keeps retrying); SIGTERM ends the wait with exit code 0."""
        threading.Timer(0.2, os.kill, (os.getpid(), signal.SIGTERM)).start()

        self.assertEqual(virtual_devices.main(), virtual_devices.EXIT_SUCCESS)

        self.mqtt_client.publish.assert_not_called()
        self.mqtt_client.stop.assert_called_once_with()
