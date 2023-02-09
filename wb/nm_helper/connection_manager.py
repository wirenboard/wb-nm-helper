import datetime
import json
import logging
import signal
import subprocess
import sys
import time
from io import BytesIO
from typing import Dict, Iterator, List, Optional

import dbus
import pycurl

from wb.nm_helper.logging_filter import ConnectionStateFilter
from wb.nm_helper.modem_manager import ModemManager
from wb.nm_helper.modem_manager_interfaces import IModemManager
from wb.nm_helper.network_manager import (
    NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
    NM_ACTIVE_CONNECTION_STATE_DEACTIVATED,
    NM_SETTINGS_GSM_SIM_SLOT_DEFAULT,
    NetworkManager,
    NMActiveConnection,
    NMConnection,
    NMDevice,
)
from wb.nm_helper.network_manager_interfaces import INetworkManager

EXIT_NOT_CONFIGURED = 6

CONFIG_FILE = "/etc/wb-connection-manager.conf"
CHECK_PERIOD = datetime.timedelta(seconds=5)
CONNECTION_ACTIVATION_RETRY_TIMEOUT = datetime.timedelta(seconds=60)
DEFAULT_STICKY_SIM_PERIOD = datetime.timedelta(minutes=15)
DEVICE_WAITING_TIMEOUT = datetime.timedelta(seconds=30)
CONNECTION_ACTIVATION_TIMEOUT = datetime.timedelta(seconds=30)
CONNECTION_DEACTIVATION_TIMEOUT = datetime.timedelta(seconds=30)
LOG_RATE_LIMIT_DEFAULT = datetime.timedelta(seconds=600)
DEFAULT_CONNECTIVITY_CHECK_URL = "http://network-test.debian.org/nm"
DEFAULT_CONNECTIVITY_CHECK_PAYLOAD = "NetworkManager is online"


class ImproperlyConfigured(ValueError):
    pass


class ConnectionTier:  # pylint: disable=R0903
    def __init__(self, name: str, priority: int, connections: List):
        self.name = name
        self.priority = priority
        self.connections = connections.copy()

    def get_route_metric(self):
        return (100 * (4 - self.priority)) + 5


class ConnectionManagerConfigFile:
    def __init__(self, cfg: Dict) -> None:
        self.debug: bool = cfg.get("debug", False)
        self.tiers: List[ConnectionTier] = list(self.get_tiers(cfg))
        self.sticky_sim_period: datetime.timedelta = self.get_sticky_sim_period(cfg)
        self.connectivity_check_url: str = self.get_connectivity_check_url(cfg)
        self.connectivity_check_payload: str = self.get_connectivity_check_payload(cfg)

    @staticmethod
    def get_tiers(cfg: Dict) -> List[ConnectionTier]:
        tiers = []
        for name, level in (("high", 3), ("medium", 2), ("low", 1)):
            tiers.append(ConnectionTier(name, level, cfg.get("tiers", {}).get(name, [])))
        return tiers

    @staticmethod
    def get_sticky_sim_period(cfg: Dict) -> datetime.timedelta:
        if cfg.get("sticky_sim_period_s"):
            seconds = cfg.get("sticky_sim_period_s")
            try:
                value = datetime.timedelta(seconds=int(seconds))
            except Exception as e:
                raise ImproperlyConfigured("Incorrect sticky_sim_period_s ({}): {}".format(seconds, e)) from e
        else:
            value = DEFAULT_STICKY_SIM_PERIOD
        logging.debug("Initialized sticky_sim_period as %s seconds", value)
        return value

    @staticmethod
    def get_connectivity_check_url(cfg: Dict) -> str:
        value = cfg.get("connectivity_check_url", DEFAULT_CONNECTIVITY_CHECK_URL)
        if not value.startswith("http://") and not value.startswith("https://"):
            raise ImproperlyConfigured("Bad connectivity URL %s" % value)
        return value

    @staticmethod
    def get_connectivity_check_payload(cfg: Dict) -> str:
        value = cfg.get("connectivity_check_payload", DEFAULT_CONNECTIVITY_CHECK_PAYLOAD)
        if not value:
            raise ImproperlyConfigured("Empty connectivity payload")
        return value

    def has_connections(self) -> bool:
        for tier in self.tiers:
            if len(tier.connections):
                return True
        return False


class TimeoutManager:
    def __init__(self, config: ConnectionManagerConfigFile) -> None:
        self.config: ConnectionManagerConfigFile = config
        self.connection_retry_timeouts = {}
        self.deny_sim_switch_until: Optional[datetime.datetime] = None
        self.connection_activation_timeout = CONNECTION_ACTIVATION_TIMEOUT

    @staticmethod
    def now():
        return datetime.datetime.now()

    def debug_log_timeouts(self):
        logging.debug("GSM Sticky Timeout: %s", self.deny_sim_switch_until)
        for connection, timeout in self.connection_retry_timeouts.items():
            logging.debug("Connection Retry Timeout for %s: %s", connection, timeout)

    def touch_connection_retry_timeout(self, cn_id):
        self.connection_retry_timeouts[cn_id] = self.now() + CONNECTION_ACTIVATION_RETRY_TIMEOUT

    def reset_connection_retry_timeout(self, cn_id):
        self.connection_retry_timeouts[cn_id] = self.now()

    def touch_gsm_timeout(self, con: NMConnection) -> None:
        if con.get_connection_type() == "gsm":
            self.deny_sim_switch_until = self.now() + self.config.sticky_sim_period
            logging.info(
                "New active connection is GSM, not changing SIM slots until %s",
                self.deny_sim_switch_until.isoformat(),
            )
        else:
            self.deny_sim_switch_until = None
            logging.debug("Active connection is not GSM, sticky SIM timeout cleared")

    def connection_retry_timeout_is_active(self, cn_id):
        if (
            cn_id not in self.connection_retry_timeouts
            or self.connection_retry_timeouts.get(cn_id) < self.now()
        ):
            logging.debug("Connection retry timeout is not active for connection %s", cn_id)
            return False
        logging.debug("Connection retry timeout is active for connection %s", cn_id)
        return True

    def gsm_sticky_timeout_is_active(self) -> bool:
        if self.deny_sim_switch_until and self.deny_sim_switch_until > self.now():
            logging.debug("Sticky GSM SIM slot timeout is active")
            return True
        return False


# pylint: disable=too-many-instance-attributes
class ConnectionManager:
    def __init__(
        self,
        network_manager: INetworkManager,
        config: ConnectionManagerConfigFile,
        modem_manager: IModemManager,
    ) -> None:
        self.network_manager: INetworkManager = network_manager
        self.modem_manager: IModemManager = modem_manager
        self.config: ConnectionManagerConfigFile = config
        self.timeouts: TimeoutManager = TimeoutManager(config)
        self.current_tier: Optional[ConnectionTier] = None
        self.current_connection: Optional[str] = None

    def cycle_loop(self):
        new_tier, new_connection, changed = self.check()
        if changed:
            self.set_current_connection(new_connection, new_tier)
            self.deactivate_lesser_gsm_connections(new_connection, new_tier)
            self.apply_metrics()

    def check(self) -> (ConnectionTier, str, bool):
        logging.debug("check(): starting iteration")
        self.timeouts.debug_log_timeouts()
        for tier in self.config.tiers:
            # first, if tier is current, check current connection
            if self.current_tier and self.current_connection and self.current_tier.priority == tier.priority:
                try:
                    active_cn = self.find_activated_connection(self.current_connection)
                    if active_cn and self.check_connectivity(active_cn):
                        logging.debug(
                            "Current connection %s is most preferred and has connectivity",
                            self.current_connection,
                        )
                        return self.current_tier, self.current_connection, False
                except dbus.exceptions.DBusException as ex:
                    self._log_connection_check_error(self.current_connection, ex)
            # second, iterate all connections in tier
            for cn_id in tier.connections:
                if (
                    self.current_tier
                    and tier.priority == self.current_tier.priority
                    and cn_id == self.current_connection
                ):
                    # current connection was already checked above
                    continue
                try:
                    active_cn = self.find_activated_connection(cn_id)
                    if not active_cn and self.ok_to_activate_connection(cn_id):
                        active_cn = self.activate_connection(cn_id)
                        self.timeouts.touch_connection_retry_timeout(cn_id)
                    if active_cn and self.check_connectivity(active_cn):
                        return tier, cn_id, True
                except dbus.exceptions.DBusException as ex:
                    self._log_connection_check_error(cn_id, ex)
                    self.timeouts.touch_connection_retry_timeout(cn_id)

        # no working connections found at all
        return self.current_tier, self.current_connection, False

    def ok_to_activate_connection(self, cn_id: str) -> bool:
        if self.timeouts.connection_retry_timeout_is_active(cn_id):
            logging.debug("Retry timeout is still effective for %s", cn_id)
            return False
        if self.connection_is_gsm(cn_id) and self.timeouts.gsm_sticky_timeout_is_active():
            logging.debug(
                "SIM switch disabled until %s, not changing SIM",
                self.timeouts.deny_sim_switch_until.isoformat(),
            )
            return False
        logging.debug("It is ok to activate connection %s", cn_id)
        return True

    @staticmethod
    def _log_connection_check_error(cn_id: str, e: Exception) -> None:
        data = {"cn_id": cn_id}
        logging.warning('Error during connection "%s" checking: %s', cn_id, e, extra=data)

    def find_active_connection(self, cn_id: str) -> Optional[NMActiveConnection]:
        return self.network_manager.get_active_connections().get(cn_id)

    def find_activated_connection(self, cn_id: str) -> Optional[NMActiveConnection]:
        active_cn = self.find_active_connection(cn_id)
        if active_cn and active_cn.get_property("State") != NM_ACTIVE_CONNECTION_STATE_ACTIVATED:
            return None
        return active_cn

    def activate_connection(self, cn_id: str) -> Optional[NMActiveConnection]:
        logging.debug("Trying to activate connection %s", cn_id)
        activation_fns = {
            "gsm": self._activate_gsm_connection,
            "802-3-ethernet": self._activate_generic_connection,
            "802-11-wireless": self._activate_generic_connection,
        }
        con = self.find_connection(cn_id)
        if not con:
            return None
        dev = self._find_device_for_connection(con, cn_id)
        if not dev:
            return None
        connection_type = con.get_settings().get("connection").get("type")
        activate_fn = activation_fns.get(connection_type)
        if not activate_fn:
            extra = {
                "rate_limit_tag": "ACT_FN_NOT_FOUND_" + cn_id,
                "rate_limit_timeout": LOG_RATE_LIMIT_DEFAULT,
            }
            logging.warning(
                'Activation function for connection "%s" (%s) not found', cn_id, connection_type, extra=extra
            )
            return None
        con = activate_fn(dev, con)
        if con:
            logging.debug("Activated connection %s", cn_id)
        return con

    def find_connection(self, cn_id):
        con = self.network_manager.find_connection(cn_id)
        if not con:
            extra = {"rate_limit_tag": "CON_NOT_FOUND_" + cn_id, "rate_limit_timeout": LOG_RATE_LIMIT_DEFAULT}
            logging.warning('Connection "%s" not found', cn_id, extra=extra)
        return con

    def _find_device_for_connection(self, con: NMConnection, cn_id: str) -> Optional[NMDevice]:
        dev = self.network_manager.find_device_for_connection(con)
        if not dev:
            extra = {"rate_limit_tag": "DEV_NOT_FOUND_" + cn_id, "rate_limit_timeout": LOG_RATE_LIMIT_DEFAULT}
            logging.warning('Device for connection %s" not found', cn_id, extra=extra)
        return dev

    def _activate_generic_connection(self, dev: NMDevice, con: NMConnection) -> Optional[NMActiveConnection]:
        active_connection = self.network_manager.activate_connection(con, dev)
        if self._wait_generic_connection_activation(
            active_connection, self.timeouts.connection_activation_timeout
        ):
            return active_connection
        return None

    @staticmethod
    def _wait_generic_connection_activation(con: NMActiveConnection, timeout) -> bool:
        logging.debug("Waiting for connection activation")
        start = datetime.datetime.now()
        while start + timeout >= datetime.datetime.now():
            current_state = con.get_property("State")
            if current_state == NM_ACTIVE_CONNECTION_STATE_ACTIVATED:
                return True
            logging.debug("state: %s", current_state)
            time.sleep(1)
        return False

    def _activate_gsm_connection(self, dev: NMDevice, con: NMConnection) -> Optional[NMActiveConnection]:
        dev_path = dev.get_property("Udi")
        logging.debug('Device path "%s"', dev_path)
        # Switching SIM card while other connection is active can cause NM restart
        # So deactivate active connection if it exists
        active_connection = dev.get_active_connection()
        if active_connection:
            self.deactivate_current_gsm_connection(active_connection)
        else:
            logging.debug("No active gsm connection detected")
        sim_slot = self.get_sim_slot(con)
        current_sim_slot = self.modem_manager.get_primary_sim_slot(dev_path)
        logging.debug("Current SIM slot: %s, new SIM slot: %s", str(current_sim_slot), str(sim_slot))
        if sim_slot not in (NM_SETTINGS_GSM_SIM_SLOT_DEFAULT, current_sim_slot):
            logging.debug("Will change SIM slot to %s", sim_slot)
            dev = self.change_modem_sim_slot(dev, con, sim_slot)
        if not dev:
            return None
        active_connection = self.network_manager.activate_connection(con, dev)
        if self._wait_connection_activation(active_connection, self.timeouts.connection_activation_timeout):
            return active_connection
        return None

    def deactivate_connection(self, active_cn: NMActiveConnection) -> None:
        self.network_manager.deactivate_connection(active_cn)
        self._wait_connection_deactivation(active_cn, CONNECTION_DEACTIVATION_TIMEOUT)

    @staticmethod
    def get_sim_slot(con: NMConnection) -> str:
        settings = con.get_settings()
        if "sim-slot" in settings["gsm"]:
            return settings["gsm"]["sim-slot"]
        return NM_SETTINGS_GSM_SIM_SLOT_DEFAULT

    def change_modem_sim_slot(self, dev: NMDevice, con: NMConnection, sim_slot: str) -> Optional[NMDevice]:
        dev_path = dev.get_property("Udi")
        if not self.modem_manager.set_primary_sim_slot(dev_path, sim_slot):
            return None
        # After switching SIM card MM recreates device with new path
        dev = self._wait_gsm_sim_slot_to_change(con, dev_path, str(sim_slot), DEVICE_WAITING_TIMEOUT)
        if not dev:
            logging.debug("Failed to get new device after changing SIM slot")
            return None
        dev_path = dev.get_property("Udi")
        logging.debug('Device path after SIM switching "%s"', dev_path)
        return dev

    def deactivate_current_gsm_connection(self, active_connection):
        logging.debug("Active gsm connection is %s", active_connection)
        old_active_connection_id = active_connection.get_connection_id()
        logging.debug('Deactivate active connection "%s"', old_active_connection_id)
        self.timeouts.reset_connection_retry_timeout(old_active_connection_id)
        self.network_manager.deactivate_connection(active_connection)
        self._wait_connection_deactivation(active_connection, CONNECTION_DEACTIVATION_TIMEOUT)
        if self.current_connection == old_active_connection_id:
            logging.debug("We deactivated current connection, resetting current connection pointer")
            self.current_connection = None
            self.current_tier = None
        else:
            logging.debug("We deactivated non-current connection")

    def _wait_gsm_sim_slot_to_change(
        self, con: NMConnection, dev_path: str, sim_slot: str, timeout: datetime.timedelta
    ) -> Optional[NMDevice]:
        logging.debug("Waiting for GSM device path %s to change", dev_path)
        start = datetime.datetime.now()
        while start + timeout >= datetime.datetime.now():
            try:
                dev = self.network_manager.find_device_for_connection(con)
                if not dev:
                    continue
                new_dev_path = dev.get_property("Udi")
                logging.debug("Current device path: %s", new_dev_path)
                if dev_path != new_dev_path:
                    logging.debug("Device path changed from %s to %s", dev_path, new_dev_path)
                    logging.info("Changed SIM slot to %s to check connectivity", sim_slot)
                    return dev
            except dbus.exceptions.DBusException as ex:
                # Some exceptions can be raised during waiting, because MM and NM remove and create devices
                logging.debug("Error during device waiting: %s", ex)
            time.sleep(1)
        logging.debug("Timeout reached while trying to change SIM slot")
        return None

    @staticmethod
    def _wait_connection_activation(con: NMActiveConnection, timeout) -> bool:
        logging.debug("Waiting for connection activation")
        start = datetime.datetime.now()
        while start + timeout >= datetime.datetime.now():
            current_state = con.get_property("State")
            if current_state == NM_ACTIVE_CONNECTION_STATE_ACTIVATED:
                return True
            time.sleep(1)
        return False

    @staticmethod
    def _wait_connection_deactivation(con: NMActiveConnection, timeout) -> None:
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

    def connection_is_gsm(self, cn_id: str) -> bool:
        con = self.network_manager.find_connection(cn_id)
        if con:
            value = con.get_settings().get("connection").get("type") == "gsm"
            logging.debug("Connection %s is gsm: %s", cn_id, value)
            return value
        logging.debug("Connection %s not found", cn_id)
        return False

    def set_current_connection(self, cn_id: str, tier: ConnectionTier):
        if self.current_connection != cn_id:
            self.timeouts.touch_gsm_timeout(self.network_manager.find_connection(cn_id))
            self.current_connection = cn_id
            self.current_tier = tier
            logging.info("Current connection changed to %s", cn_id)
        logging.debug("Current connection is the same (%s), not changing", cn_id)

    def deactivate_lesser_gsm_connections(self, cn_id: str, tier: ConnectionTier) -> None:
        connections = list(self.find_lesser_gsm_connections(cn_id, tier))
        for connection in connections:
            data = {"cn_id": connection.get_connection_id()}
            self.deactivate_connection(connection)
            logging.info('"%s" is deactivated', cn_id, extra=data)

    def find_lesser_gsm_connections(
        self, current_con_id: str, current_tier: ConnectionTier
    ) -> Iterator[NMActiveConnection]:
        for tier in [item for item in self.config.tiers if item.priority <= current_tier.priority]:
            for cn_id in [
                item for item in tier.connections if item != current_con_id and self.connection_is_gsm(item)
            ]:
                active_cn = self.find_active_connection(cn_id)
                if active_cn:
                    yield active_cn

    @staticmethod
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
    def check_connectivity(self, active_cn: NMActiveConnection) -> bool:
        ifaces = active_cn.get_ifaces()
        logging.debug("interfaces for %s: %s", active_cn.get_connection_id(), ifaces)
        if ifaces and ifaces[0]:
            try:
                payload = self.curl_get(ifaces[0], self.config.connectivity_check_url)
                logging.debug("Payload is %s", payload)
                answer_is_ok = self.config.connectivity_check_payload in payload
                logging.debug("Connectivity via %s is %s", ifaces[0], answer_is_ok)
                return answer_is_ok
            except pycurl.error as ex:
                logging.debug("Error during %s connectivity check: %s", ifaces[0], ex)
        else:
            logging.debug("Connection %s seems to have no interfaces", active_cn.get_connection_id())
        return False

    def apply_metrics(self):
        active_connections = self.network_manager.get_active_connections()
        for tier in self.config.tiers:
            for cn_id in tier.connections:
                active_cn = active_connections.get(cn_id)
                if not active_cn:
                    continue
                if self.current_connection == cn_id:
                    metric = 55
                else:
                    metric = tier.get_route_metric()
                self.set_device_metric_for_connection(active_cn, metric)

    def set_device_metric_for_connection(self, active_cn: NMActiveConnection, metric: int) -> None:
        logging.debug("Set device metric for connection %s (%s)", active_cn.get_connection_id(), str(metric))
        devices = active_cn.get_devices()
        if len(devices) < 1:
            logging.debug("No devices found for connection %s", active_cn.get_connection_id())
            return
        device = devices[0]
        if active_cn.get_connection().get_connection_type() == "gsm":
            iface = device.get_property("IpInterface")
            self.call_ifmetric(iface, metric)
        else:
            device.set_metric(metric)

    @staticmethod
    def call_ifmetric(iface, metric):
        subprocess.run(["/usr/sbin/ifmetric", iface, str(metric)], shell=False, check=False)


def main():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as file:
            cfg_json = json.load(file)
        config = ConnectionManagerConfigFile(cfg_json)
    except (
        FileNotFoundError,
        PermissionError,
        OSError,
        json.decoder.JSONDecodeError,
        ImproperlyConfigured,
    ) as ex:
        logging.error("Loading %s failed: %s", CONFIG_FILE, ex)
        sys.exit(EXIT_NOT_CONFIGURED)

    log_level = logging.DEBUG if config.debug else logging.INFO
    if log_level > logging.DEBUG:
        logger = logging.getLogger()
        logger.addFilter(ConnectionStateFilter())
    logging.basicConfig(level=log_level)

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    if config.has_connections():
        manager = ConnectionManager(
            network_manager=NetworkManager(), config=config, modem_manager=ModemManager()
        )
        while True:
            manager.cycle_loop()
            time.sleep(CHECK_PERIOD.total_seconds())
    else:
        logging.info("Nothing to manage")


if __name__ == "__main__":
    main()
