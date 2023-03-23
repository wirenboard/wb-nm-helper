import json
import logging

from connection import Connection
from wb_common.mqtt_client import MQTTClient


class VirtualDevice:
    def _get_virtual_device_name(self, uuid):
        return "system__networks__" + uuid

    def _get_device_topic(self, uuid):
        return "/devices/" + self._get_virtual_device_name(uuid)

    def _get_control_topic(self, uuid, control_name):
        return self._get_device_topic(uuid) + "/controls/" + control_name

    def _create_device(self):
        self._mqtt_client.publish(
            self._get_device_topic(self._connection.uuid), self._connection.uuid, retain=True
        )
        self._mqtt_client.publish(
            self._get_device_topic(self._connection.uuid) + "/meta/name",
            "Network Connection " + self._connection.name,
            retain=True,
        )
        self._mqtt_client.publish(
            self._get_device_topic(self._connection.uuid) + "/meta/driver", "wb-nm-helper", retain=True
        )

    def _remove_device(self):
        self._mqtt_client.publish(
            self._get_device_topic(self._connection.uuid) + "/meta/driver", None, retain=True
        )
        self._mqtt_client.publish(
            self._get_device_topic(self._connection.uuid) + "/meta/name", None, retain=True
        )
        self._mqtt_client.publish(self._get_device_topic(self._connection.uuid), None, retain=True)

    def _create_control(
        self,
        control_name,
        control_title,
        control_type,
        order,
        value,
        readonly=True,
    ):
        meta = {
            "order": order,
            "title": {"en": control_title},
            "type": control_type,
            "readonly": readonly,
        }

        self._publish_control_meta(control_name, meta)
        self._publish_control_data(control_name, value)

    def _remove_control(self, control_name):
        self._mqtt_client.publish(
            self._get_control_topic(self._connection.uuid, control_name), None, retain=True
        )
        self._mqtt_client.publish(
            self._get_control_topic(self._connection.uuid, control_name) + "/meta", None, retain=True
        )

    def _publish_control_data(self, control_name, value):
        self._mqtt_client.publish(
            self._get_control_topic(self._connection.uuid, control_name), value, retain=True
        )

    def _publish_control_meta(self, control_name, meta):
        meta_json = json.dumps(meta)
        self._mqtt_client.publish(
            self._get_control_topic(self._connection.uuid, control_name) + "/meta", meta_json, retain=True
        )

    def _updown_message_callback(self, client, userdata, message):
        self._up_down_meta["readonly"] = True
        self._publish_control_meta("UpDown", self._up_down_meta)

        self._connection_activity_switch(self._connection, self._up_down_meta["title"]["en"] == "Up")

        self._up_down_meta["readonly"] = False
        self._publish_control_meta("UpDown", self._up_down_meta)

    def _add_control_message_callback(self, control_name):
        self._mqtt_client.subscribe(self._get_control_topic(self._connection.uuid, control_name) + "/on")
        self._mqtt_client.message_callback_add(
            self._get_control_topic(self._connection.uuid, control_name) + "/on",
            self._updown_message_callback,
        )

    def __init__(
        self,
        mqtt_client: MQTTClient,
        connection: Connection,
        connection_activity_switch,
        logger: logging.Logger,
    ):
        self._logger = logger
        self._mqtt_client = mqtt_client
        self._connection = connection
        self._connection_activity_switch = connection_activity_switch

        self._create_device()
        self._create_control("Name", "Name", "text", 1, self._connection.name)
        self._create_control("UUID", "UUID", "text", 2, self._connection.uuid)
        self._create_control("Type", "Type", "text", 3, self._connection.type)
        self._create_control("Active", "Active", "switch", 4, "1" if connection.active else "0")
        self._create_control("Device", "Device", "text", 5, self._connection.device)
        self._create_control(
            "State",
            "State",
            "text",
            6,
            self._connection.state.name.lower() if self._connection.state is not None else None,
        )
        self._create_control("Address", "IP", "text", 7, self._connection.ip4addresses)
        self._create_control(
            "Connectivity", "Connectivity", "switch", 8, "1" if self._connection.connectivity else "0"
        )

        self._up_down_meta = {
            "order": 9,
            "title": {"en": "Down" if self._connection.active else "Up"},
            "type": "pushbutton",
            "readonly": False,
        }
        self._publish_control_meta("UpDown", self._up_down_meta)
        self._add_control_message_callback("UpDown")

        self._logger.info("New virtual device %s %s", self._connection.name, self._connection.uuid)

    def remove(self):
        self._remove_control("UpDown")
        self._remove_control("Connectivity")
        self._remove_control("Address")
        self._remove_control("State")
        self._remove_control("Device")
        self._remove_control("Active")
        self._remove_control("Type")
        self._remove_control("UUID")
        self._remove_control("Name")
        self._remove_device()
        self._logger.debug("Remove virtual device %s %s", self._connection.name, self._connection.uuid)

    def update(self):
        self._publish_control_data("Active", "1" if self._connection.active else "0")
        self._publish_control_data("Device", self._connection.device)
        self._publish_control_data(
            "State",
            self._connection.state.name.lower() if self._connection.state is not None else None,
        )
        self._publish_control_data("Address", self._connection.ip4addresses)

        self._up_down_meta["title"]["en"] = "Down" if self._connection.active else "Up"
        self._publish_control_meta("UpDown", self._up_down_meta)

        self._publish_control_data("Connectivity", "1" if self._connection.connectivity else "0")

        self._logger.debug(
            "Update virtual device settings for %s %s", self._connection.name, self._connection.uuid
        )
