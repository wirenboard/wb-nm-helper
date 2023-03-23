import argparse
import logging
import signal
import sys

from connection import Connection
from dbus_client import DbusClient
from virtual_device import VirtualDevice
from wb_common.mqtt_client import DEFAULT_BROKER_URL, MQTTClient


class VirtualDevicesClientException(Exception):
    pass


class VirtualDevicesClient:

    DEVICES_UUID_SUBSCRIBE_TOPIC = "/devices/+/controls/UUID"

    def __init__(self, logger):
        self._logger = logger
        self._virtual_devices = {}
        self._mqtt_client = None
        self._dbus_client = None

        self._mqtt_client = MQTTClient("connections-virtual-devices", DEFAULT_BROKER_URL)
        self._dbus_client = DbusClient(
            self._add_new_connection_callback,
            self._remove_connection_callback,
            self._update_connection_callback,
            logger,
        )

    def run(self):
        try:
            self._mqtt_client.start()
            self._dbus_client.initialize_connections()
            self._subscribe_to_devices()
            self._dbus_client.run_event_loop()
        except (TimeoutError, ConnectionRefusedError) as error:
            self._logger.error("MQTT client connection error")
            raise VirtualDevicesClientException from error

    def stop(self):
        self._dbus_client.stop_event_loop()
        self._mqtt_client.stop()

    def _on_mqtt_device_uuid_topic_message(self, client, userdata, message):
        uuid = message.payload.decode("utf-8")

        if uuid == "":
            return

        virtual_devices_list = [
            device for connection, device in self._virtual_devices.items() if connection.uuid == uuid
        ]
        if len(virtual_devices_list) == 0:
            self._logger.info("Find old virtual device for %s connection uuid, remove it", uuid)
            # create fake device to remove old device
            old_virtual_device = VirtualDevice(
                self._mqtt_client, Connection("", uuid, None, self._logger), None, self._logger
            )
            old_virtual_device.remove()

    def _subscribe_to_devices(self):
        self._mqtt_client.subscribe(self.DEVICES_UUID_SUBSCRIBE_TOPIC)
        self._mqtt_client.message_callback_add(
            self.DEVICES_UUID_SUBSCRIBE_TOPIC, self._on_mqtt_device_uuid_topic_message
        )

    def _unsubscribe_from_devices(self):
        self._mqtt_client.unsubscribe(self.DEVICES_UUID_SUBSCRIBE_TOPIC)
        self._mqtt_client.message_callback_remove(self.DEVICES_UUID_SUBSCRIBE_TOPIC)

    def _add_new_connection_callback(self, connection: Connection):
        self._unsubscribe_from_devices()
        self._virtual_devices[connection] = VirtualDevice(
            self._mqtt_client, connection, self._connection_activity_switch, self._logger
        )
        self._subscribe_to_devices()

    def _remove_connection_callback(self, connection: Connection):
        self._virtual_devices[connection].remove()
        self._virtual_devices.pop(connection)
        return

    def _update_connection_callback(self, connection: Connection):
        self._virtual_devices[connection].update()

    def _connection_activity_switch(self, connection: Connection, enable):
        self._dbus_client.connection_activity_switch(connection, enable)


def main(argv=sys.argv):
    parser = argparse.ArgumentParser(description="Service for creating virtual connection devices")
    parser.add_argument(
        "--debug",
        help="Enable debug output",
        default=False,
        dest="debug",
        required=False,
        action="store_true",
    )
    options = parser.parse_args(argv[1:])

    if options.debug:
        logger_level = logging.DEBUG
    else:
        logger_level = logging.INFO

    logger = logging.getLogger("virtual-connections_client")
    logger.setLevel(logger_level)
    stream = logging.StreamHandler()
    stream.setLevel(logger_level)
    logger.addHandler(stream)

    virtual_connections_client = VirtualDevicesClient(logger)

    def stop_virtual_connections_client(signum, msg):
        virtual_connections_client.stop()

    signal.signal(signal.SIGINT, stop_virtual_connections_client)
    signal.signal(signal.SIGTERM, stop_virtual_connections_client)

    try:
        virtual_connections_client.run()
    except (KeyboardInterrupt, VirtualDevicesClientException):
        pass
    finally:
        logger.info("Stopping")
        virtual_connections_client.stop()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
