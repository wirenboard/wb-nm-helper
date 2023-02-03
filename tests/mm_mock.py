# pylint: disable=duplicate-code

import json
import logging

from wb.nm_helper.network_manager import (
    NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
    NM_ACTIVE_CONNECTION_STATE_ACTIVATING,
    NM_ACTIVE_CONNECTION_STATE_DEACTIVATED,
    NM_ACTIVE_CONNECTION_STATE_UNKNOWN,
)


class AbsFakeNMConnection:
    def __init__(self, name, net_man):
        self.name = name
        self.net_man = net_man

    def data(self):
        return self.net_man.connections.get(self.name)

    def get_settings(self):
        settings = {"connection": {"type": self.data().get("device_type")}}
        if self.data().get("device_type") == "gsm":
            settings["gsm"] = {"sim-slot": self.data().get("sim_slot")}
        return settings

    def get_connection_type(self):
        return self.get_settings().get("connection").get("type")

    def get_connection_id(self):
        return self.name


class FakeNMActiveConnection(AbsFakeNMConnection):
    def get_property(self, param):
        if param == "State":
            return self.data().get("connection_state")
        return None

    def get_devices(self):
        if self.data().get("connection_state") == NM_ACTIVE_CONNECTION_STATE_ACTIVATED:
            return [self.net_man.get_device_for_connection(self)]
        return []

    def get_ifaces(self):
        res = []
        for device in self.get_devices():
            logging.warning("get ifaces: device is %s", device.name)
            res.append(self.net_man.get_iface_for_device(device))
        logging.warning("get ifaces: ifaces are %s", res)
        return res


class FakeNMConnection(AbsFakeNMConnection):
    pass


class FakeNMDevice:
    def __init__(self, name, net_man):
        self.name = name
        self.net_man: FakeNetworkManager = net_man

    def data(self):
        return self.net_man.devices.get(self.name)

    def increase_index(self):
        self.data()["index"] += 1

    def set_metric(self, metric):
        self.data()["metric"] = metric

    def get_property(self, param):
        if param == "Udi":
            return "/fake/Devices/{}/{}".format(self.name, self.data().get("index"))
        if param == "IpInterface":
            return self.net_man.get_iface_for_device(self)
        return None

    def get_active_connection(self):
        con = self.net_man.get_connection_for_device(self)
        if con.data().get("device_type") == "gsm":
            logging.info("connections:")
            logging.info(json.dumps(self.net_man.connections))
            for name, data in self.net_man.connections.items():
                if (
                        data.get("sim_slot") == self.net_man.gsm_sim_slot
                        and data.get("connection_state") == NM_ACTIVE_CONNECTION_STATE_ACTIVATED
                ):
                    logging.info("Active Device GSM connection is %s", name)
                    return FakeNMActiveConnection(name, self.net_man)
            return None
        logging.info("Active Device connection is %s", con.name)
        return con


class FakeNetworkManager:
    def __init__(self):
        self.connections = {}
        self.devices = {}
        self.ifaces = {}
        self.gsm_sim_slot = 1
        self.modem_device = None

    def get_device_metric(self, device_name):
        return self.devices.get(device_name).get("metric")

    def add_connection(
            self,
            name,
            device_type,
            device_connected=False,
            connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN,
            **kwargs
    ):
        device_name = kwargs.get("device_name")
        if not device_name:
            device_name = "dev_" + name
        iface_name = kwargs.get("iface_name")
        if not iface_name:
            iface_name = "if_" + name

        self.connections[name] = {
            "device_type": device_type,
            "device_connected": device_connected,
            "connection_state": connection_state,
            "device_name": device_name,
            "iface_name": iface_name,
        }
        for kwarg, value in kwargs.items():
            self.set_connection_param(name, kwarg, value)

        if device_name not in self.devices:
            self.devices[device_name] = {"index": 1, "sim_slot": 1, "metric": -1}
        if iface_name not in self.ifaces:
            self.ifaces[iface_name] = {"metric": -1}

    def set_connection_param(self, name, param, value):
        self.connections[name][param] = value

    def add_wifi_client(
            self, name, device_connected=False, connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN, **kwargs
    ):
        self.add_connection(
            name,
            device_type="802-11-wireless",
            device_connected=device_connected,
            connection_state=connection_state,
            **kwargs,
        )

    def add_ethernet(
            self, name, device_connected=False, connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN, **kwargs
    ):
        self.add_connection(
            name,
            device_type="802-3-ethernet",
            device_connected=device_connected,
            connection_state=connection_state,
            **kwargs,
        )

    def add_gsm(
            self,
            name,
            device_connected=False,
            connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN,
            sim_slot=1,
            **kwargs
    ):
        if not kwargs.get("device_name"):
            kwargs["device_name"] = "ttyUSB1"
        if not kwargs.get("iface_name"):
            kwargs["iface_name"] = "ppp0"
        self.add_connection(
            name,
            device_type="gsm",
            device_connected=device_connected,
            connection_state=connection_state,
            sim_slot=sim_slot,
            **kwargs,
        )

    def get_active_connections(self):
        logging.debug("get_active_connections()")
        output = {}
        for name, data in self.connections.items():
            logging.debug("Connection %s is %s", name, data.get("connection_state"))
            if data.get("connection_state") in (
                    NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
                    NM_ACTIVE_CONNECTION_STATE_ACTIVATING,
            ):
                output[name] = FakeNMActiveConnection(name, self)
        logging.debug(output)
        return output

    def deactivate_connection(self, con):
        name = con.get_connection_id()
        self.set_connection_param(name, "connection_state", NM_ACTIVE_CONNECTION_STATE_DEACTIVATED)

    def find_connection(self, cn_id):
        if cn_id in self.connections:
            return FakeNMConnection(cn_id, self)
        return None

    @staticmethod
    def find_device_for_connection(con):
        if con.data().get("device_connected"):
            dev = FakeNMDevice(con.data().get("device_name"), con.net_man)
            return dev
        return None

    def activate_connection(self, con, dev):
        logging.warning("activate connection %s (%s)", con.name, dev.name)
        if con.name not in self.connections:
            raise Exception("No connection found: {}".format(con.name))
        if not self.connections.get(con.name).get("device_connected"):
            self.connections[con.name]["connection_state"] = NM_ACTIVE_CONNECTION_STATE_DEACTIVATED
        if self.connections.get(con.name).get("device_type") == "gsm" and self.connections.get(con.name).get(
                "sim_slot"
        ) != dev.data().get("sim_slot"):
            self.connections[con.name]["connection_state"] = NM_ACTIVE_CONNECTION_STATE_UNKNOWN
        elif self.connections.get(con.name).get("should_stuck_activating"):
            self.connections[con.name]["connection_state"] = NM_ACTIVE_CONNECTION_STATE_ACTIVATING
        else:
            self.connections[con.name]["connection_state"] = NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        logging.warning(self.connections[con.name])
        return FakeNMActiveConnection(con.name, self)

    def get_connection_for_device(self, device):
        for cn_id, data in self.connections.items():
            if data.get("device_name") == device.name:
                return FakeNMActiveConnection(cn_id, self)
        return None

    def get_iface_for_device(self, device):
        for data in self.connections.values():
            if data.get("device_name") == device.name:
                return data.get("iface_name")
        return None

    def get_device_for_connection(self, connection):
        return FakeNMDevice(self.connections.get(connection.name).get("device_name"), self)


class FakeModemManager:
    def __init__(self, net_man):
        self.net_man = net_man

    def get_primary_sim_slot(self, dev_path):
        dev_name = dev_path.split("/")[3]
        return self.net_man.devices.get(dev_name).get("sim_slot")

    def set_primary_sim_slot(self, dev_path, sim_slot):
        dev_name = dev_path.split("/")[3]
        device = FakeNMDevice(dev_name, self.net_man)
        for data in self.net_man.connections.values():
            if (
                    data.get("device_type") == "gsm"
                    and data.get("device_connected")
                    and data.get("sim_slot") == sim_slot
            ):
                logging.warning("set primary sim slot %s, %s", dev_path, sim_slot)
                logging.warning("Set SIM slot %s", str(sim_slot))
                device.increase_index()
                device.data()["sim_slot"] = sim_slot
                return True
        return False
