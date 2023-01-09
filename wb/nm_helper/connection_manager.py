from __future__ import annotations

import datetime
import json
import logging
import signal
import sys
import time
from io import BytesIO
from typing import Dict, List, Optional

import dbus
import pycurl

from .modem_manager import ModemManager
from .network_manager import (
    NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
    NM_ACTIVE_CONNECTION_STATE_DEACTIVATED,
    NM_SETTINGS_GSM_SIM_SLOT_DEFAULT,
    NetworkManager,
    NMActiveConnection,
    NMConnection,
    NMDevice,
)

EXIT_NOTCONFIGURED = 6

# Settings
CHECK_PERIOD = datetime.timedelta(seconds=5)
DEFAULT_STICKY_SIM_PERIOD = datetime.timedelta(minutes=15)
CONNECTION_ACTIVATION_RETRY_TIMEOUT = datetime.timedelta(seconds=60)
DEVICE_WAITING_TIMEOUT = datetime.timedelta(seconds=30)
CONNECTION_ACTIVATION_TIMEOUT = datetime.timedelta(seconds=30)
CONNECTION_DEACTIVATION_TIMEOUT = datetime.timedelta(seconds=30)
CONNECTIVITY_CHECK_URL = "http://network-test.debian.org/nm"
CONFIG_FILE = "/etc/wb-connection-manager.conf"


class ConnectionStateFilter(logging.Filter):
    # pylint: disable=too-few-public-methods
    def __init__(self):
        logging.Filter.__init__(self)
        self.last_event = {}

    def filter(self, record):
        if "cn_id" in record.__dict__:
            cn_id = record.__dict__["cn_id"]
            if cn_id in self.last_event:
                if self.last_event[cn_id] == record.msg:
                    return False
            self.last_event[cn_id] = record.msg
        return True


def get_sim_slot(con: NMConnection):
    settings = con.get_settings()
    if "sim-slot" in settings["gsm"]:
        return settings["gsm"]["sim-slot"]
    return NM_SETTINGS_GSM_SIM_SLOT_DEFAULT


def wait_connection_activation(con: NMActiveConnection, timeout) -> bool:
    logging.debug("Waiting for connection activation")
    start = datetime.datetime.now()
    while start + timeout >= datetime.datetime.now():
        current_state = con.get_property("State")
        if current_state == NM_ACTIVE_CONNECTION_STATE_ACTIVATED:
            return True
        time.sleep(1)
    return False


def wait_connection_deactivation(con: NMActiveConnection, timeout) -> None:
    logging.debug("Waiting for connection deactivation")
    start = datetime.datetime.now()
    while start + timeout >= datetime.datetime.now():
        try:
            current_state = con.get_property("State")
            if current_state == NM_ACTIVE_CONNECTION_STATE_DEACTIVATED:
                return
        except dbus.exceptions.DBusException as ex:
            if ex.get_dbus_name() == "org.freedesktop.DBus.Error.UnknownMethod":
                # Connection object is already removed from bus
                return
        time.sleep(1)


def get_active_connections(
    connection_ids: List[str], active_connections: Dict[str, NMActiveConnection]
) -> Dict[str, NMActiveConnection]:
    res = {}
    for cn_id, connection in active_connections.items():
        if cn_id in connection_ids:
            res[cn_id] = connection
    return res


def curl_get(iface: str, url: str) -> str:
    buffer = BytesIO()
    curl = pycurl.Curl()
    curl.setopt(curl.URL, url)
    curl.setopt(curl.WRITEDATA, buffer)
    curl.setopt(curl.INTERFACE, iface)
    curl.perform()
    curl.close()
    return buffer.getvalue().decode("UTF-8")


# Simple implementation that mimics NM behavior
# NM reports limited connectivity for all gsm ppp connections
# https://wirenboard.bitrix24.ru/workgroups/group/218/tasks/task/view/53068/
# Use NM's implementation after fixing the bug
def check_connectivity(active_cn: NMActiveConnection) -> bool:
    ifaces = active_cn.get_ifaces()
    logging.debug('interfaces for {}: {}'.format(active_cn.get_connection_id(), ifaces))
    if ifaces and ifaces[0]:
        try:
            ok = curl_get(ifaces[0], CONNECTIVITY_CHECK_URL).startswith("NetworkManager is online")
            logging.debug('Connectivity via {} is {}'.format(ifaces[0], ok))
            return ok
        except pycurl.error as ex:
            logging.debug("Error during {} connectivity check: {}".format(ifaces[0], ex))
    else:
        logging.debug('Connection {} seems to have no interfaces'.format(active_cn.get_connection_id()))
    return False


def log_active_connections(active_connections: Dict[str, NMActiveConnection]):
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug("Active connections")
        for cn_id, con in active_connections.items():
            logging.debug("%s %s", cn_id, con.get_path())


class ConnectionManager:

    def __init__(self, network_manager: NetworkManager, cfg: Dict) -> None:
        self.network_manager = network_manager
        self.connection_up_time = {}
        self.connection_priority = cfg.get('connections', [])
        self.sticky_sim_period = None
        self.deny_sim_switch_until = None
        self.current_connection = None
        self.initialize_sticky_sim_period(cfg)

    def initialize_sticky_sim_period(self, cfg):
        if cfg.get('sticky_sim_period'):
            self.sticky_sim_period = datetime.timedelta(seconds=int(cfg.get('sticky_sim_period')))
        else:
            self.sticky_sim_period = DEFAULT_STICKY_SIM_PERIOD
        logging.debug(
            'Initialized sticky_sim_period as {} seconds'.format(self.sticky_sim_period.total_seconds()))

    def wait_gsm_device_for_connection(
        self, con: NMConnection, dev_path: str, timeout: datetime.timedelta
    ) -> Optional[NMDevice]:
        logging.debug("Waiting for GSM device path {} to change".format(dev_path))
        start = datetime.datetime.now()
        while start + timeout >= datetime.datetime.now():
            try:
                dev = self.network_manager.find_device_for_connection(con)
                if dev:
                    new_dev_path = dev.get_property("Udi")
                    logging.debug("Current device path: {}".format(new_dev_path))
                    if dev_path != new_dev_path:
                        logging.debug('Device path changed from {} to {}'.format(dev_path, new_dev_path))
                        logging.info('Changed SIM slot')
                        return dev
            except dbus.exceptions.DBusException as ex:
                # Some exceptions can be raised during waiting, because MM and NM remove and create devices
                logging.debug("Error during device waiting: %s", ex)
            time.sleep(1)
        logging.info("Timeout reached while trying to change SIM slot")
        return None

    def activate_gsm_connection(self, dev: NMDevice, con: NMConnection) -> Optional[NMActiveConnection]:
        dev_path = dev.get_property("Udi")
        logging.debug('Device path "%s"', dev_path)
        # Switching SIM card while other connection is active can cause NM restart
        # So deactivate active connection if it exists
        active_connection = dev.get_active_connection()
        if active_connection:
            logging.debug('Active gsm connection is {}'.format(active_connection))
            if active_connection.get_connection_type() == 'gsm' and self.deny_sim_switch_until \
                    and self.deny_sim_switch_until > datetime.datetime.now():
                logging.info('SIM switch disabled until {}, not changing SIM'.format(self.deny_sim_switch_until.isoformat()))
                return None
            old_active_connection_id = active_connection.get_connection_id()
            logging.debug('Deactivate active connection "%s"', old_active_connection_id)
            self.connection_up_time[old_active_connection_id] = datetime.datetime.now()
            self.network_manager.deactivate_connection(active_connection)
            wait_connection_deactivation(active_connection, CONNECTION_DEACTIVATION_TIMEOUT)
        else:
            logging.debug('No active gsm connection detected')
        modem_manager = ModemManager()
        sim_slot = get_sim_slot(con)
        current_sim_slot = modem_manager.get_primary_sim_slot(dev_path)
        if sim_slot != NM_SETTINGS_GSM_SIM_SLOT_DEFAULT and sim_slot != current_sim_slot:
            if not modem_manager.set_primary_sim_slot(dev_path, sim_slot):
                return None
            # After switching SIM card MM recreates device with new path
            dev = self.wait_gsm_device_for_connection(con, dev_path, DEVICE_WAITING_TIMEOUT)
            if not dev:
                logging.debug("New device for connection is not found")
                return None
            dev_path = dev.get_property("Udi")
            logging.debug('Device path after SIM switching "%s"', dev_path)
        active_connection = self.network_manager.activate_connection(con, dev)
        if wait_connection_activation(active_connection, CONNECTION_ACTIVATION_TIMEOUT):
            return active_connection
        return None

    def activate_generic_connection(self, dev: NMDevice, con: NMConnection) -> Optional[NMActiveConnection]:
        active_connection = self.network_manager.activate_connection(con, dev)
        if wait_connection_activation(active_connection, CONNECTION_ACTIVATION_TIMEOUT):
            return active_connection
        return None

    def is_time_to_activate(self, cn_id: str) -> bool:
        if cn_id in self.connection_up_time:
            if self.connection_up_time[cn_id] > datetime.datetime.now():
                return False
        return True

    def activate_connection(self, cn_id: str) -> NMActiveConnection:
        logging.debug('Trying to activate connection {}'.format(cn_id))
        activation_fns = {
            "gsm": self.activate_gsm_connection,
            "802-3-ethernet": self.activate_generic_connection,
            "802-11-wireless": self.activate_generic_connection,
        }
        con = self.network_manager.find_connection(cn_id)
        if not con:
            logging.warning('"%s" is not found', cn_id)
            return None
        dev = self.network_manager.find_device_for_connection(con)
        if not dev:
            logging.warning('Device for connection "%s" is not found', cn_id)
            return None
        settings = con.get_settings()
        activate_fn = activation_fns.get(settings["connection"]["type"])
        if activate_fn:
            con = activate_fn(dev, con)
            if con:
                logging.info('Activated connection {}'.format(cn_id))
        return con

    def deactivate_connection(self, active_cn: NMActiveConnection) -> None:
        self.network_manager.deactivate_connection(active_cn)
        wait_connection_deactivation(active_cn, CONNECTION_DEACTIVATION_TIMEOUT)

    def deactivate_connections(self, connections: Dict[str, NMActiveConnection]) -> None:
        for cn_id, con in connections.items():
            self.deactivate_connection(con)
            data = {"cn_id": cn_id}
            logging.info('"%s" is deactivated', cn_id, extra=data)

    def check(self) -> None:
        logging.debug('check(): starting iteration')
        logging.debug('GSM_STICKY_TIMEOUT: {}'.format(self.deny_sim_switch_until))
        for k in self.connection_up_time:
            logging.debug('CONNECTION_UP_TIME[{}]: {}'.format(k, self.connection_up_time[k]))
        for index, cn_id in enumerate(self.connection_priority):
            data = {"cn_id": cn_id}
            try:
                logging.debug('Checking connection {}'.format(cn_id))
                active_connections = self.network_manager.get_active_connections()
                log_active_connections(active_connections)
                active_cn = None
                if cn_id in active_connections and \
                        active_connections.get(cn_id).get_property("State") == NM_ACTIVE_CONNECTION_STATE_ACTIVATED:
                    active_cn = active_connections[cn_id]
                    logging.debug('Found {} as already active'.format(cn_id))
                else:

                    if self.is_time_to_activate(cn_id):
                        logging.debug('Will try to activate {} for check'.format(cn_id))
                        active_cn = self.activate_connection(cn_id)
                        self.hit_connection_up_time(cn_id)
                    else:
                        logging.debug('No time to activate {} yet'.format(cn_id))
                if active_cn:
                    if check_connectivity(active_cn):
                        logging.debug('Connection "%s" has connectivity', cn_id, extra=data)
                        try:
                            less_priority_connections = get_active_connections(
                                self.connection_priority[index + 1:], active_connections
                            )
                            self.deactivate_connections(less_priority_connections)
                        except dbus.exceptions.DBusException as ex:
                            # Not a problem if less priority connections still be active
                            logging.warning("Error during connections deactivation: %s", ex)
                        if self.current_connection != cn_id:
                            self.current_connection_changed(active_cn, cn_id)
                        return
                    else:
                        self.deactivate_connection(active_cn)
                        logging.info('"%s" has limited connectivity', cn_id, extra=data)


            # Something went wrong during connection checking.
            # Proceed to next connection to be always on-line
            except dbus.exceptions.DBusException as ex:
                logging.warning('Error during connection "%s" checking: %s', cn_id, ex, extra=data)
                self.hit_connection_up_time(cn_id)
            except Exception as ex:  # pylint: disable=broad-except
                logging.critical(
                    'Error during connection "%s" checking: %s', cn_id, ex, extra=data, exc_info=True
                )
                self.hit_connection_up_time(cn_id)

    def hit_connection_up_time(self, cn_id):
        self.connection_up_time[cn_id] = datetime.datetime.now() + CONNECTION_ACTIVATION_RETRY_TIMEOUT

    def current_connection_changed(self, active_cn, cn_id):
        logging.info('Current connection changed to {}'.format(cn_id))
        if active_cn.get_connection_type() == 'gsm':
            self.deny_sim_switch_until = datetime.datetime.now() + self.sticky_sim_period
            logging.info('New active connection is GSM, not changing SIM slots until {}'.format(
                self.deny_sim_switch_until.isoformat()
            ))
        else:
            self.deny_sim_switch_until = None
            logging.debug('Active connection is not GSM, sticky SIM timeout cleared')
        self.current_connection = cn_id


def main():
    cfg = {}
    try:
        with open(CONFIG_FILE, encoding="utf-8") as file:
            cfg = json.load(file)
    except (FileNotFoundError, PermissionError, OSError, json.decoder.JSONDecodeError) as ex:
        logging.error("Loading %s failed: %s", CONFIG_FILE, ex)
        sys.exit(EXIT_NOTCONFIGURED)

    log_level = logging.DEBUG if cfg.get("debug", False) else logging.INFO
    if log_level > logging.DEBUG:
        logger = logging.getLogger()
        logger.addFilter(ConnectionStateFilter())
    logging.basicConfig(level=log_level)

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    if len(cfg.get("connections", [])) > 0:
        manager = ConnectionManager(NetworkManager(), cfg)
        while True:
            manager.check()
            time.sleep(CHECK_PERIOD.total_seconds())
    else:
        logging.info("Nothing to manage")


if __name__ == "__main__":
    main()
