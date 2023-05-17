import datetime
import importlib
import logging
import unittest
from unittest.mock import MagicMock, call

from tests.mm_mock import (
    FakeModemManager,
    FakeNetworkManager,
    FakeNMActiveConnection,
    FakeNMConnection,
)
from wb.nm_helper import connection_manager
from wb.nm_helper.connection_manager import (
    CONNECTION_ACTIVATION_RETRY_TIMEOUT,
    ConnectionManager,
    NetworkAwareConfigFile,
)
from wb.nm_helper.network_manager import (
    NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
    NM_ACTIVE_CONNECTION_STATE_ACTIVATING,
    NM_ACTIVE_CONNECTION_STATE_DEACTIVATED,
    NM_ACTIVE_CONNECTION_STATE_UNKNOWN,
    NMActiveConnection,
    NMConnection,
)

logging.basicConfig(level=logging.DEBUG)

DEFAULT_CONFIG = {
    "tiers": {
        "high": ["wb-eth0", "wb-eth1"],
        "medium": ["wb-wifi-client"],
        "low": ["wb-gsm-sim1", "wb-gsm-sim2"],
    }
}

TEST_NOW = datetime.datetime(year=2000, month=1, day=1)
SHORT_TIMEOUT = datetime.timedelta(seconds=5)


class AbsConManTests(unittest.TestCase):
    config: NetworkAwareConfigFile = None
    net_man: FakeNetworkManager = None
    mod_man = FakeModemManager = None
    con_man = ConnectionManager = None

    def setUp(self) -> None:
        importlib.reload(connection_manager)
        self.net_man = FakeNetworkManager()
        self.mod_man = FakeModemManager(self.net_man)
        connection_manager.curl_get = MagicMock()

    def tearDown(self) -> None:
        importlib.reload(connection_manager)

    def _init_con_man(self, config_data):
        self.config = NetworkAwareConfigFile(network_manager=self.net_man)
        self.config.load_config(cfg=config_data)
        self.con_man = ConnectionManager(self.net_man, self.config, modem_manager=self.mod_man)
        self.con_man.timeouts.connection_activation_timeout = SHORT_TIMEOUT
        self.con_man.timeouts.now = MagicMock(return_value=TEST_NOW)
        self.con_man.call_ifmetric = MagicMock()

    def _is_active_connection(self, con):
        return isinstance(con, (NMActiveConnection, FakeNMActiveConnection))

    def _is_connection(self, con):
        return isinstance(con, (NMConnection, FakeNMConnection))


class IntegratedTests(AbsConManTests):
    def test_10_loop_gsm_disconnect_lesser_modems(self):
        self.net_man.fake_add_ethernet("wb-eth0", device_connected=False)
        self.net_man.fake_add_ethernet("wb-eth1", device_connected=False)
        self.net_man.fake_add_wifi_client("wb-wifi-client", device_connected=False)
        self.net_man.fake_add_gsm(
            "wb-gsm1-sim1",
            device_connected=True,
            connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
            sim_slot=1,
            device_name="ttyUSB1",
            iface_name="ppp0",
        )
        self.net_man.fake_add_gsm(
            "wb-gsm2-sim1",
            device_connected=True,
            connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
            sim_slot=1,
            device_name="ttyUSB2",
            iface_name="ppp1",
        )
        local_config = {
            "tiers": {
                "high": ["wb-eth0", "wb-gsm1-sim1"],
                "medium": ["wb-wifi-client"],
                "low": ["wb-gsm2-sim1", "wb-eth1"],
            }
        }
        self._init_con_man(local_config)
        connection_manager.curl_get.side_effect = [self.con_man.config.connectivity_check_payload]
        self.con_man.current_connection = "wb-gsm2-sim1"
        self.con_man.current_tier = self.config.tiers[2]
        assert len(self.con_man.network_manager.get_active_connections()) == 2

        self.con_man.cycle_loop()

        curl_calls = [
            call("ppp0", self.con_man.config.connectivity_check_url),
        ]
        assert connection_manager.curl_get.mock_calls == curl_calls
        assert self.con_man.call_ifmetric.mock_calls == [
            call("ppp0", 55),
        ]
        assert self.con_man.current_tier == self.config.tiers[0]
        assert self.con_man.current_connection == "wb-gsm1-sim1"
        assert (
            self.net_man.connections.get("wb-gsm1-sim1").get("connection_state")
            == NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        )
        assert (
            self.net_man.connections.get("wb-gsm2-sim1").get("connection_state")
            == NM_ACTIVE_CONNECTION_STATE_DEACTIVATED
        )

    def test_09_loop_metrics(self):
        self.net_man.fake_add_ethernet(
            "wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        )
        self.net_man.fake_add_ethernet(
            "wb-eth1", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        )
        self.net_man.fake_add_ethernet(
            "wb-eth2", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        )
        self.net_man.fake_add_wifi_client(
            "wb-wifi-client", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        )
        self.net_man.fake_add_gsm(
            "wb-gsm1-sim1",
            device_connected=True,
            connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
            sim_slot=1,
            device_name="ttyUSB1",
            iface_name="ppp0",
        )
        self.net_man.fake_add_gsm(
            "wb-gsm2-sim1",
            device_connected=True,
            connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
            sim_slot=1,
            device_name="ttyUSB2",
            iface_name="ppp1",
        )
        local_config = {
            "tiers": {
                "high": ["wb-eth0", "wb-gsm1-sim1"],
                "medium": ["wb-wifi-client"],
                "low": ["wb-gsm2-sim1", "wb-eth1", "wb-eth2"],
            }
        }
        self._init_con_man(local_config)
        connection_manager.curl_get.side_effect = ["", "", "", self.con_man.config.connectivity_check_payload]
        self.con_man.current_connection = "wb-gsm1-sim1"
        self.con_man.current_tier = self.config.tiers[0]
        assert len(self.con_man.network_manager.get_active_connections()) == 6

        self.con_man.cycle_loop()

        assert self.con_man.current_tier == self.config.tiers[2]
        assert self.con_man.current_connection == "wb-gsm2-sim1"
        assert self.net_man.fake_get_device_metric("dev_wb-eth0") == 105
        assert self.net_man.fake_get_device_metric("dev_wb-eth1") == 305
        assert self.net_man.fake_get_device_metric("dev_wb-eth2") == 306
        assert self.net_man.fake_get_device_metric("dev_wb-wifi-client") == 205
        curl_calls = [
            call("ppp0", self.con_man.config.connectivity_check_url),
            call("if_wb-eth0", self.con_man.config.connectivity_check_url),
            call("if_wb-wifi-client", self.con_man.config.connectivity_check_url),
            call("ppp1", self.con_man.config.connectivity_check_url),
        ]
        assert connection_manager.curl_get.mock_calls == curl_calls
        assert self.con_man.call_ifmetric.mock_calls == [call("ppp0", 106), call("ppp1", 55)]
        assert (
            self.net_man.connections.get("wb-gsm1-sim1").get("connection_state")
            == NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        )
        assert (
            self.net_man.connections.get("wb-gsm2-sim1").get("connection_state")
            == NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        )


class ConnectionIsStickyTests(AbsConManTests):
    def test_01_connection_is_sticky(self):
        self.net_man.fake_add_gsm("wb-gsm-sim1")
        self.net_man.fake_add_wifi_client("wb-wifi-client")
        self.net_man.fake_add_ethernet("wb-eth0")
        self._init_con_man(DEFAULT_CONFIG)

        assert self.con_man.connection_is_sticky("eth0") is False
        assert self.con_man.connection_is_sticky("wb-gsm-sim1") is True
        assert self.con_man.connection_is_sticky("wb-wifi-client") is True
        assert self.con_man.connection_is_sticky("non-entity") is False


class ConnectionIsGSMTests(AbsConManTests):
    def test_01_connection_is_gsm(self):
        self.net_man.fake_add_gsm("wb-gsm-sim1")
        self.net_man.fake_add_ethernet("wb-eth0")
        self._init_con_man(DEFAULT_CONFIG)

        assert self.con_man.connection_is_gsm("eth0") is False
        assert self.con_man.connection_is_gsm("wb-gsm-sim1") is True
        assert self.con_man.connection_is_gsm("non-entity") is False


class OKToActivateTests(AbsConManTests):
    def test_02_ok_to_activate(self):
        self._init_con_man(DEFAULT_CONFIG)
        self.net_man.fake_add_ethernet(
            "wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        )
        self.net_man.fake_add_wifi_client(
            "wb-wifi-client", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        )
        self.net_man.fake_add_gsm(
            "wb-gsm-sim1",
            device_connected=True,
            connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
            sim_slot=1,
        )
        self.con_man.timeouts.now = MagicMock(return_value=TEST_NOW)
        self.con_man.timeouts.connection_retry_timeouts = {}
        self.con_man.timeouts.keep_sticky_connections_until = None

        assert self.con_man.ok_to_activate_connection("wb-eth0") is True
        assert self.con_man.ok_to_activate_connection("wb-wifi-client") is True
        assert self.con_man.ok_to_activate_connection("wb-gsm-sim1") is True

        self.con_man.timeouts.connection_retry_timeouts = {
            "wb-eth0": TEST_NOW - CONNECTION_ACTIVATION_RETRY_TIMEOUT,
            "wb-gsm-sim1": TEST_NOW - CONNECTION_ACTIVATION_RETRY_TIMEOUT,
        }

        assert self.con_man.ok_to_activate_connection("wb-eth0") is True
        assert self.con_man.ok_to_activate_connection("wb-gsm-sim1") is True

        self.con_man.timeouts.connection_retry_timeouts = {
            "wb-eth0": TEST_NOW + CONNECTION_ACTIVATION_RETRY_TIMEOUT,
            "wb-gsm-sim1": TEST_NOW + CONNECTION_ACTIVATION_RETRY_TIMEOUT,
        }
        assert self.con_man.ok_to_activate_connection("wb-eth0") is False
        assert self.con_man.ok_to_activate_connection("wb-gsm-sim1") is False

        self.con_man.timeouts.connection_retry_timeouts = {}
        self.con_man.timeouts.keep_sticky_connections_until = (
            TEST_NOW + self.con_man.config.sticky_connection_period
        )
        assert self.con_man.ok_to_activate_connection("wb-wifi-client") is False
        assert self.con_man.ok_to_activate_connection("wb-gsm-sim1") is False

        self.con_man.timeouts.connection_retry_timeouts = {
            "wb-gsm-sim1": TEST_NOW - CONNECTION_ACTIVATION_RETRY_TIMEOUT
        }
        self.con_man.timeouts.keep_sticky_connections_until = (
            TEST_NOW + self.con_man.config.sticky_connection_period
        )
        assert self.con_man.ok_to_activate_connection("wb-gsm-sim1") is False

        self.con_man.timeouts.connection_retry_timeouts = {
            "wb-gsm-sim1": TEST_NOW + CONNECTION_ACTIVATION_RETRY_TIMEOUT
        }
        self.con_man.timeouts.keep_sticky_connections_until = (
            TEST_NOW + self.con_man.config.sticky_connection_period
        )
        assert self.con_man.ok_to_activate_connection("wb-gsm-sim1") is False


class GetActiveConnectionTests(AbsConManTests):
    def test_03_get_active_connection_1(self):
        self._init_con_man(DEFAULT_CONFIG)
        self.net_man.fake_add_ethernet(
            "wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        )
        self.net_man.fake_add_ethernet("wb-eth1", device_connected=True)
        self.net_man.fake_add_gsm(
            "wb-gsm-sim1",
            device_connected=True,
            connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
            sim_slot=1,
        )
        self.net_man.fake_add_gsm(
            "wb-gsm-sim2",
            device_connected=True,
            connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN,
            sim_slot=2,
        )

        result1 = self.con_man.find_active_connection("wb-eth0")
        result2 = self.con_man.find_active_connection("wb-eth1")
        result3 = self.con_man.find_active_connection("wb-gsm-sim1")
        result4 = self.con_man.find_active_connection("wb-gsm-sim2")

        assert self._is_active_connection(result1)
        assert result1.get_connection_id() == "wb-eth0"
        assert result2 is None
        assert self._is_active_connection(result3)
        assert result3.get_connection_id() == "wb-gsm-sim1"
        assert result4 is None

    def test_04_get_active_connection_2(self):
        self._init_con_man(DEFAULT_CONFIG)
        self.net_man.fake_add_ethernet(
            "wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        )
        self.net_man.fake_add_ethernet(
            "wb-eth1", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATING
        )

        result1 = self.con_man.find_activated_connection("wb-eth0")
        result2 = self.con_man.find_activated_connection("wb-eth1")
        assert self._is_active_connection(result1)
        assert result1.get_connection_id() == "wb-eth0"
        assert result2 is None
