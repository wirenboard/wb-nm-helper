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
from dbus import DBusException

from wb.nm_helper.logging_filter import ConnectionStateFilter
from wb.nm_helper.modem_manager import ModemManager
from wb.nm_helper.modem_manager_interfaces import IModemManager
from wb.nm_helper.network_manager import (
    NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
    NM_ACTIVE_CONNECTION_STATE_DEACTIVATED,
    NM_DEVICE_TYPE_ETHERNET,
    NM_DEVICE_TYPE_MODEM,
    NM_DEVICE_TYPE_WIFI,
    NM_SETTINGS_GSM_SIM_SLOT_DEFAULT,
    NetworkManager,
    NMActiveConnection,
    NMConnection,
    NMDevice,
    connection_type_to_device_type,
)
from wb.nm_helper.network_manager_interfaces import INetworkManager

EXIT_NOT_CONFIGURED = 6

CONNECTIVITY_CHECK_TIMEOUT = 15
LOGGING_FORMAT = "%(message)s"
CONFIG_FILE = "/etc/wb-connection-manager.conf"
CHECK_PERIOD = datetime.timedelta(seconds=5)
CONNECTION_ACTIVATION_RETRY_TIMEOUT = datetime.timedelta(seconds=60)
DEFAULT_STICKY_CONNECTION_PERIOD = datetime.timedelta(minutes=15)
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

    def get_base_route_metric(self):
        return (100 * (4 - self.priority)) + 5

    def update_connections(self, new_connections: List[str]):
        self.connections = new_connections


class ConfigFile:
    def __init__(self) -> None:
        self.debug = False
        self.tiers: List[ConnectionTier] = []
        self.sticky_connection_period: Optional[datetime.timedelta] = None
        self.connectivity_check_url = ""
        self.connectivity_check_payload = ""

    def load_config(self, cfg: Dict):
        self.debug = cfg.get("debug", False)
        if cfg.get("tiers"):
            self.tiers = list(self.get_tiers(cfg))
        self.sticky_connection_period = self.get_sticky_connection_period(cfg)
        self.connectivity_check_url = self.get_connectivity_check_url(cfg)
        self.connectivity_check_payload = self.get_connectivity_check_payload(cfg)

    def get_tiers(self, cfg: Dict) -> List[ConnectionTier]:
        tiers = []
        for name, level in (("high", 3), ("medium", 2), ("low", 1)):
            items = []
            for cn_id in cfg.get("tiers", {}).get(name, []):
                items.append(cn_id)
            tiers.append(ConnectionTier(name, level, items))
        return tiers

    @staticmethod
    def get_sticky_connection_period(cfg: Dict) -> datetime.timedelta:
        if cfg.get("sticky_connection_period_s"):
            seconds = cfg.get("sticky_connection_period_s")
            try:
                value = datetime.timedelta(seconds=int(seconds))
            except Exception as e:
                raise ImproperlyConfigured(
                    "Incorrect sticky_connection_period_s ({}): {}".format(seconds, e)
                ) from e
        else:
            value = DEFAULT_STICKY_CONNECTION_PERIOD
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


class NetworkAwareConfigFile(ConfigFile):
    def __init__(self, network_manager: INetworkManager) -> None:
        super().__init__()
        self.network_manager = network_manager

    def load_config(self, cfg: Dict):
        super().load_config(cfg)
        if not self.tiers:
            self.tiers = self.get_default_tiers()
        self.filter_out_unmanaged_connections()

    def filter_out_unmanaged_connections(self):
        for tier in self.tiers:
            new_items = []
            for cn_id in tier.connections:
                con = self.network_manager.find_connection(cn_id)
                if not con:
                    logging.warning("Connection %s not found, skipping", cn_id)
                    continue
                if self.is_connection_unmanaged(con):
                    logging.warning("Connection %s is unmanaged, skipping", cn_id)
                    continue
                new_items.append(cn_id)
            if new_items != tier.connections:
                tier.update_connections(new_items)

    def get_default_tiers(self):
        tiers = []
        for name, level in (("high", 3), ("medium", 2), ("low", 1)):
            tiers.append(ConnectionTier(name, level, []))
        for item in self.network_manager.get_connections():
            autoconnect = item.get_settings().get("connection").get("autoconnect", True)
            never_default = item.get_settings().get("ipv4").get("never-default")
            connection_type = item.get_connection_type()
            device_type = connection_type_to_device_type(connection_type)
            connection_id = str(item.get_settings().get("connection").get("id"))
            unmanaged = self.is_connection_unmanaged(item)
            if not autoconnect or never_default or unmanaged:
                continue
            if device_type == NM_DEVICE_TYPE_MODEM:
                tiers[2].connections.append(connection_id)
            elif device_type == NM_DEVICE_TYPE_WIFI:
                tiers[1].connections.append(connection_id)
            elif device_type == NM_DEVICE_TYPE_ETHERNET:
                tiers[0].connections.append(connection_id)
            else:
                logging.warning("Unknown connection type: %s", connection_type)
        logging.debug(
            "get_default_tiers: high %s, medium %s, low %s",
            tiers[0].connections,
            tiers[1].connections,
            tiers[2].connections,
        )
        return tiers

    def is_connection_unmanaged(self, con):
        assert con, "Connection cannot be empty"
        cn_id = con.get_settings().get("connection").get("id")
        device = self.network_manager.find_device_for_connection(con)
        if not device:
            logging.warning("No device for connection %s found, assuming unmnaged", cn_id)
            return True
        managed = device.get_property("Managed")
        iface_name = device.get_property("Interface")
        if managed in (True, 1):
            logging.debug("Device for connection %s (%s) is managed, will use it", cn_id, iface_name)
            return False
        logging.warning("Device for interface %s (%s) is unmanaged, not using it", cn_id, iface_name)
        return True


class TimeoutManager:
    def __init__(self, config: ConfigFile) -> None:
        self.config: ConfigFile = config
        self.connection_retry_timeouts = {}
        self.keep_sticky_connections_until: Optional[datetime.datetime] = None
        self.connection_activation_timeout = CONNECTION_ACTIVATION_TIMEOUT

    @staticmethod
    def now():
        return datetime.datetime.now()

    def debug_log_timeouts(self):
        logging.debug("Sticky Connections Timeout: %s", self.keep_sticky_connections_until)
        for connection, timeout in self.connection_retry_timeouts.items():
            logging.debug("Connection Retry Timeout for %s: %s", connection, timeout)

    def touch_connection_retry_timeout(self, cn_id):
        self.connection_retry_timeouts[cn_id] = self.now() + CONNECTION_ACTIVATION_RETRY_TIMEOUT

    def reset_connection_retry_timeout(self, cn_id):
        self.connection_retry_timeouts[cn_id] = self.now()

    def touch_sticky_timeout(self, con: NMConnection) -> None:
        if connection_type_to_device_type(con.get_connection_type()) in (
            NM_DEVICE_TYPE_MODEM,
            NM_DEVICE_TYPE_WIFI,
        ):
            self.keep_sticky_connections_until = self.now() + self.config.sticky_connection_period
            logging.info(
                "New active connection is Sticky (GSM/Wifi), not changing SIM/Wifi connections until %s",
                self.keep_sticky_connections_until.isoformat(),
            )
        else:
            self.keep_sticky_connections_until = None
            logging.debug("Active connection is not Sticky (GSM/Wifi), sticky SIM/Wifi timeout cleared")

    def connection_retry_timeout_is_active(self, cn_id):
        if (
            cn_id not in self.connection_retry_timeouts
            or self.connection_retry_timeouts.get(cn_id) < self.now()
        ):
            logging.debug("Connection retry timeout is not active for connection %s", cn_id)
            return False
        logging.debug("Connection retry timeout is active for connection %s", cn_id)
        return True

    def sticky_timeout_is_active(self) -> bool:
        if self.keep_sticky_connections_until and self.keep_sticky_connections_until > self.now():
            logging.debug("Sticky connection timeout is active")
            return True
        return False


def read_config_json():
    with open(CONFIG_FILE, encoding="utf-8") as file:
        return json.load(file)


def curl_get(iface: str, url: str) -> str:
    buffer = BytesIO()
    curl = pycurl.Curl()
    curl.setopt(curl.URL, url)
    curl.setopt(curl.WRITEDATA, buffer)
    curl.setopt(curl.INTERFACE, iface)
    curl.setopt(pycurl.CONNECTTIMEOUT, CONNECTIVITY_CHECK_TIMEOUT)
    curl.setopt(pycurl.TIMEOUT, CONNECTIVITY_CHECK_TIMEOUT)
    curl.perform()
    curl.close()
    return buffer.getvalue().decode("UTF-8")

    # Simple implementation that mimics NM behavior
    # NM reports limited connectivity for all gsm ppp connections
    # https://wirenboard.bitrix24.ru/workgroups/group/218/tasks/task/view/53068/
    # Use NM's implementation after fixing the bug


def check_connectivity(active_cn: NMActiveConnection, config: ConfigFile = None) -> bool:
    if not config:
        config = ConfigFile()
        config.load_config(read_config_json())
    ifaces = active_cn.get_ifaces()
    logging.debug("interfaces for %s: %s", active_cn.get_connection_id(), ifaces)
    if ifaces and ifaces[0]:
        try:
            payload = curl_get(ifaces[0], config.connectivity_check_url)
            logging.debug("Payload is %s", payload)
            answer_is_ok = config.connectivity_check_payload in payload
            logging.debug("Connectivity via %s is %s", ifaces[0], answer_is_ok)
            return answer_is_ok
        except pycurl.error as ex:
            logging.debug("Error during %s connectivity check: %s", ifaces[0], ex)
    else:
        logging.debug("Connection %s seems to have no interfaces", active_cn.get_connection_id())
    return False


class ConnectionManager:  # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        network_manager: INetworkManager,
        config: NetworkAwareConfigFile,
        modem_manager: IModemManager,
    ) -> None:
        self.network_manager: INetworkManager = network_manager
        self.modem_manager: IModemManager = modem_manager
        self.config: NetworkAwareConfigFile = config
        self.timeouts: TimeoutManager = TimeoutManager(config)
        self.current_tier: Optional[ConnectionTier] = None
        self.current_connection: Optional[str] = None
        logging.debug(
            "Initialized sticky_connection_period as %s seconds",
            self.config.sticky_connection_period.total_seconds(),
        )

    def cycle_loop(self):
        new_tier, new_connection = self.check()
        if new_connection != self.current_connection or new_tier != self.current_tier:
            self.set_current_connection(new_connection, new_tier)
            self.deactivate_lesser_gsm_connections(new_connection, new_tier)
            self.apply_metrics()

    def check(self) -> (ConnectionTier, str, bool):
        logging.debug("check(): starting iteration")
        self.timeouts.debug_log_timeouts()
        for tier in self.config.tiers:
            logging.debug("checking tier %s", tier.name)
            # first, if tier is current, check current connection
            if self.current_tier and self.current_connection and self.current_tier.priority == tier.priority:
                logging.debug("checking currently active connection %s", self.current_connection)
                try:
                    active_cn = self.find_activated_connection(self.current_connection)
                    if active_cn and check_connectivity(active_cn, self.config):
                        logging.debug(
                            "Current connection %s is most preferred and has connectivity",
                            self.current_connection,
                        )
                        return self.current_tier, self.current_connection
                except dbus.exceptions.DBusException as ex:
                    self._log_connection_check_error(self.current_connection, ex)
            # second, iterate all connections in tier
            for cn_id in tier.connections:
                if (
                    self.current_tier
                    and tier.priority == self.current_tier.priority
                    and cn_id == self.current_connection
                ):
                    logging.debug("current connection %s was already checked above, skipping", cn_id)
                    continue
                logging.debug("checking connection %s", cn_id)
                try:
                    active_cn = self.find_activated_connection(cn_id)
                    if not active_cn and self.ok_to_activate_connection(cn_id):
                        active_cn = self.activate_connection(cn_id)
                        self.timeouts.touch_connection_retry_timeout(cn_id)
                    if active_cn and check_connectivity(active_cn, self.config):
                        return tier, cn_id
                except dbus.exceptions.DBusException as ex:
                    self._log_connection_check_error(cn_id, ex)
                    self.timeouts.touch_connection_retry_timeout(cn_id)

        logging.debug("No working connections found at all")
        return self.current_tier, self.current_connection

    def ok_to_activate_connection(self, cn_id: str) -> bool:
        if self.timeouts.connection_retry_timeout_is_active(cn_id):
            logging.debug("Retry timeout is still effective for %s", cn_id)
            return False
        if self.connection_is_sticky(cn_id) and self.timeouts.sticky_timeout_is_active():
            logging.debug(
                "Sticky connections timeout active until %s, not touching sticky connections",
                self.timeouts.keep_sticky_connections_until.isoformat(),
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
            NM_DEVICE_TYPE_ETHERNET: self._activate_generic_connection,
            NM_DEVICE_TYPE_WIFI: self._activate_wifi_connection,
            NM_DEVICE_TYPE_MODEM: self._activate_gsm_connection,
        }
        con = self.find_connection(cn_id)
        if not con:
            return None
        dev = self._find_device_for_connection(con, cn_id)
        if not dev:
            return None
        connection_type = con.get_connection_type()
        device_type = connection_type_to_device_type(connection_type)
        activate_fn = activation_fns.get(device_type)
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
        logging.debug("Waiting for connection activation (%s)", con.get_connection_id())
        start = datetime.datetime.now()
        while start + timeout >= datetime.datetime.now():
            current_state = con.get_property("State")
            if current_state == NM_ACTIVE_CONNECTION_STATE_ACTIVATED:
                return True
            logging.debug(
                "Still waiting for connection activation (%s, state: %s)",
                con.get_connection_id(),
                current_state,
            )
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
        sim_slot = con.get_sim_slot()
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

    def _activate_wifi_connection(self, dev: NMDevice, con: NMConnection) -> Optional[NMActiveConnection]:
        # Deactivate other active wifi connection if it exists
        active_wifi_connections = self._get_active_wifi_connections()
        for active_connection in active_wifi_connections:
            logging.debug(
                "Other wifi connection %s is active, will deactivate it",
                active_connection.get_connection_id(),
            )
            self.deactivate_connection(active_connection)
        if not active_wifi_connections:
            logging.debug("No active wifi connection detected")
        active_connection = self.network_manager.activate_connection(con, dev)
        if self._wait_connection_activation(active_connection, self.timeouts.connection_activation_timeout):
            return active_connection
        return None

    def deactivate_connection(self, active_cn: NMActiveConnection) -> None:
        if active_cn.get_connection_id() == self.current_connection:
            self.current_connection = None
            self.current_tier = None
        self.network_manager.deactivate_connection(active_cn)
        self._wait_connection_deactivation(active_cn, CONNECTION_DEACTIVATION_TIMEOUT)

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
        logging.debug("Currently active gsm connection is %s", active_connection)
        old_active_connection_id = active_connection.get_connection_id()
        logging.debug('Deactivating active connection "%s" to switch SIM slot', old_active_connection_id)
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
        logging.debug("Waiting for connection activation (%s)", con.get_connection_id())
        start = datetime.datetime.now()
        while start + timeout >= datetime.datetime.now():
            current_state = con.get_property("State")
            if current_state == NM_ACTIVE_CONNECTION_STATE_ACTIVATED:
                return True
            time.sleep(1)
        return False

    @staticmethod
    def _wait_connection_deactivation(con: NMActiveConnection, timeout) -> None:
        logging.debug("Waiting for connection deactivation (%s)", con.get_connection_id())
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
            device_type = connection_type_to_device_type(con.get_connection_type())
            value = device_type == NM_DEVICE_TYPE_MODEM
            logging.debug("Connection %s is GSM: %s", cn_id, value)
            return value
        logging.debug("Connection %s not found", cn_id)
        return False

    def connection_is_sticky(self, cn_id: str) -> bool:
        con = self.network_manager.find_connection(cn_id)
        if con:
            device_type = connection_type_to_device_type(con.get_connection_type())
            value = device_type in (NM_DEVICE_TYPE_MODEM, NM_DEVICE_TYPE_WIFI)
            logging.debug("Connection %s is sticky: %s", cn_id, value)
            return value
        logging.debug("Connection %s not found", cn_id)
        return False

    def set_current_connection(self, cn_id: str, tier: ConnectionTier):
        if self.current_connection != cn_id:
            self.timeouts.touch_sticky_timeout(self.network_manager.find_connection(cn_id))
            self.current_connection = cn_id
            self.current_tier = tier
            logging.info("Current connection changed to %s", cn_id)
        logging.debug("Current connection is the same (%s), not changing", cn_id)

    def deactivate_lesser_gsm_connections(self, cn_id: str, tier: ConnectionTier) -> None:
        connections = list(self.find_lesser_gsm_connections(cn_id, tier))
        for connection in connections:
            data = {"cn_id": connection.get_connection_id()}
            self.deactivate_connection(connection)
            logging.info(
                'Deactivated unneeded GSM connection "%s" to save GSM traffic', data["cn_id"], extra=data
            )

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

    def apply_metrics(self):
        active_connections = self.network_manager.get_active_connections()
        for tier in self.config.tiers:
            tier_counter = 0
            for cn_id in tier.connections:
                active_cn = active_connections.get(cn_id)
                if not active_cn:
                    continue
                if self.current_connection == cn_id:
                    metric = 55
                else:
                    metric = tier.get_base_route_metric() + tier_counter
                    tier_counter += 1
                self.set_device_metric_for_connection(active_cn, metric)

    def set_device_metric_for_connection(self, active_cn: NMActiveConnection, metric: int) -> None:
        logging.debug("Set device metric for connection %s (%s)", active_cn.get_connection_id(), str(metric))
        devices = active_cn.get_devices()
        if len(devices) < 1:
            logging.debug("No devices found for connection %s", active_cn.get_connection_id())
            return
        device = devices[0]
        if connection_type_to_device_type(active_cn.get_connection_type()) == NM_DEVICE_TYPE_MODEM:
            iface = device.get_property("IpInterface")
            self.call_ifmetric(iface, metric)
        else:
            device.set_metric(metric)

    @staticmethod
    def call_ifmetric(iface, metric):
        subprocess.run(["/usr/sbin/ifmetric", iface, str(metric)], shell=False, check=False)

    def _get_active_wifi_connections(self):
        results = []
        for active_cn in self.network_manager.get_active_connections().values():
            if connection_type_to_device_type(active_cn.get_connection_type()) == NM_DEVICE_TYPE_WIFI:
                results.append(active_cn)
        return results


def init_logging(debug: bool):
    log_level = logging.DEBUG if debug else logging.INFO
    if log_level > logging.DEBUG:
        logger = logging.getLogger()
        logger.addFilter(ConnectionStateFilter())
    logging.basicConfig(level=log_level, format=LOGGING_FORMAT)


def main():
    network_manager = NetworkManager()
    try:
        cfg_json = read_config_json()
    except (
        FileNotFoundError,
        PermissionError,
        OSError,
        json.decoder.JSONDecodeError,
    ) as ex:
        logging.error("Loading %s failed: %s", CONFIG_FILE, ex)
        sys.exit(EXIT_NOT_CONFIGURED)

    init_logging(cfg_json.get("debug", False))  # must be initialized before NetworkAwareConfigFile

    try:
        config = NetworkAwareConfigFile(network_manager=network_manager)
        config.load_config(cfg=cfg_json)
    except ImproperlyConfigured as ex:
        logging.error("Configuration error: %s", ex)
        sys.exit(EXIT_NOT_CONFIGURED)

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    if config.has_connections():
        try:
            modem_manager = ModemManager()
        except DBusException as ex:
            modem_manager = None
            logging.warning("Unable to initialize ModemManager, GSM connections will be unavailable (%s)", ex)

        manager = ConnectionManager(
            network_manager=network_manager, config=config, modem_manager=modem_manager
        )
        while True:
            manager.cycle_loop()
            time.sleep(CHECK_PERIOD.total_seconds())
    else:
        logging.info("Nothing to manage")


if __name__ == "__main__":
    main()
