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
