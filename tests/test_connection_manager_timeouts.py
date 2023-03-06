import datetime
import unittest
from unittest.mock import patch

from tests.mm_mock import FakeNetworkManager
from wb.nm_helper.connection_manager import (
    CONNECTION_ACTIVATION_RETRY_TIMEOUT,
    ConnectionManagerConfigFile,
    TimeoutManager,
)
from wb.nm_helper.network_manager import NM_ACTIVE_CONNECTION_STATE_ACTIVATED

normal_config = {
    "tiers": {
        "high": ["wb-eth0", "wb-eth1"],
        "normal": ["wb-wifi-client"],
        "low": ["wb-gsm-sim1", "wb-gsm-sim2"],
    }
}

TEST_NOW = datetime.datetime(year=2000, month=1, day=1)


class TimeoutManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        config = ConnectionManagerConfigFile(normal_config)
        self.timeout_manager = TimeoutManager(config)
        self.network_manager = FakeNetworkManager()

    def test_01_touch_connection_retry_timeout(self):
        with patch.object(self.timeout_manager, "now") as now_mock:
            now_mock.return_value = TEST_NOW
            self.timeout_manager.touch_connection_retry_timeout("wb-eth0")
            timeout = CONNECTION_ACTIVATION_RETRY_TIMEOUT.total_seconds()
            assert isinstance(
                self.timeout_manager.connection_retry_timeouts.get("wb-eth0"), datetime.datetime
            )
            delta = self.timeout_manager.connection_retry_timeouts.get("wb-eth0") - TEST_NOW
            assert delta.total_seconds() == timeout

    def test_02_reset_connection_retry_timeout(self):
        with patch.object(self.timeout_manager, "now") as now_mock:
            now_mock.return_value = TEST_NOW
            self.timeout_manager.touch_connection_retry_timeout("wb-eth0")
            self.timeout_manager.reset_connection_retry_timeout("wb-eth0")
            assert isinstance(
                self.timeout_manager.connection_retry_timeouts.get("wb-eth0"), datetime.datetime
            )
            delta = self.timeout_manager.connection_retry_timeouts.get("wb-eth0") - TEST_NOW
            assert delta.total_seconds() == 0

    def test_03_touch_sticky_timeout(self):
        with patch.object(self.timeout_manager, "now") as now_mock:
            now_mock.return_value = TEST_NOW

            self.network_manager.fake_add_ethernet(
                "wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED
            )
            self.network_manager.fake_add_gsm(
                "wb-gsm-sim1",
                device_connected=True,
                connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
                sim_slot=1,
            )

            con = self.network_manager.find_connection("wb-eth0")
            self.timeout_manager.touch_sticky_timeout(con)
            assert self.timeout_manager.keep_sticky_connections_until is None

            con = self.network_manager.find_connection("wb-gsm-sim1")
            self.timeout_manager.touch_sticky_timeout(con)
            assert isinstance(self.timeout_manager.keep_sticky_connections_until, datetime.datetime)
            delta = self.timeout_manager.keep_sticky_connections_until - TEST_NOW
            assert (
                delta.total_seconds() == self.timeout_manager.config.sticky_connection_period.total_seconds()
            )

            con = self.network_manager.find_connection("wb-eth0")
            self.timeout_manager.touch_sticky_timeout(con)
            assert self.timeout_manager.keep_sticky_connections_until is None

    def test_04_connection_retry_timeout_is_active__empty(self):
        with patch.object(self.timeout_manager, "now") as now_mock:
            now_mock.return_value = TEST_NOW
            self.timeout_manager.connection_retry_timeouts = {}
            assert self.timeout_manager.connection_retry_timeout_is_active("wb-eth0") is False
            assert self.timeout_manager.connection_retry_timeout_is_active("wb-gsm-sim1") is False

    def test_05_connection_retry_timeout_is_active__expired(self):
        with patch.object(self.timeout_manager, "now") as now_mock:
            now_mock.return_value = TEST_NOW
            self.timeout_manager.connection_retry_timeouts = {
                "wb-eth0": TEST_NOW - CONNECTION_ACTIVATION_RETRY_TIMEOUT
            }
            assert self.timeout_manager.connection_retry_timeout_is_active("wb-eth0") is False
            assert self.timeout_manager.connection_retry_timeout_is_active("wb-gsm-sim1") is False

    def test_06_connection_retry_timeout_is_active__armed(self):
        with patch.object(self.timeout_manager, "now") as now_mock:
            now_mock.return_value = TEST_NOW
            self.timeout_manager.connection_retry_timeouts = {
                "wb-eth0": TEST_NOW + CONNECTION_ACTIVATION_RETRY_TIMEOUT
            }
            assert self.timeout_manager.connection_retry_timeout_is_active("wb-eth0") is True
            assert self.timeout_manager.connection_retry_timeout_is_active("wb-gsm-sim1") is False

    def test_07_gsm_sticky_timeout_is_active__empty(self):
        with patch.object(self.timeout_manager, "now") as now_mock:
            now_mock.return_value = TEST_NOW
            self.timeout_manager.keep_sticky_connections_until = None
            assert self.timeout_manager.sticky_timeout_is_active() is False

    def test_08_gsm_sticky_timeout_is_active__expired(self):
        with patch.object(self.timeout_manager, "now") as now_mock:
            now_mock.return_value = TEST_NOW
            self.timeout_manager.keep_sticky_connections_until = (
                TEST_NOW - self.timeout_manager.config.sticky_connection_period
            )
            assert self.timeout_manager.sticky_timeout_is_active() is False

    def test_09_gsm_sticky_timeout_is_active__active(self):
        with patch.object(self.timeout_manager, "now") as now_mock:
            now_mock.return_value = TEST_NOW
            self.timeout_manager.keep_sticky_connections_until = (
                TEST_NOW + self.timeout_manager.config.sticky_connection_period
            )
            assert self.timeout_manager.sticky_timeout_is_active() is True
