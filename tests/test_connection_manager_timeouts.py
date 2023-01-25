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
        self.tm = TimeoutManager(config)
        self.nm = FakeNetworkManager()

    def test_01_touch_connection_retry_timeout(self):

        with patch.object(self.tm, "now") as now_mock:
            now_mock.return_value = TEST_NOW
            self.tm.touch_connection_retry_timeout("wb-eth0")
            timeout = CONNECTION_ACTIVATION_RETRY_TIMEOUT.total_seconds()
            assert isinstance(self.tm.connection_retry_timeouts.get("wb-eth0"), datetime.datetime)
            delta = self.tm.connection_retry_timeouts.get("wb-eth0") - TEST_NOW
            assert delta.total_seconds() == timeout

    def test_02_reset_connection_retry_timeout(self):

        with patch.object(self.tm, "now") as now_mock:
            now_mock.return_value = TEST_NOW
            self.tm.touch_connection_retry_timeout("wb-eth0")
            self.tm.reset_connection_retry_timeout("wb-eth0")
            assert isinstance(self.tm.connection_retry_timeouts.get("wb-eth0"), datetime.datetime)
            delta = self.tm.connection_retry_timeouts.get("wb-eth0") - TEST_NOW
            assert delta.total_seconds() == 0

    def test_03_touch_gsm_timeout(self):

        with patch.object(self.tm, "now") as now_mock:
            now_mock.return_value = TEST_NOW

            self.nm.add_ethernet("wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED)
            self.nm.add_gsm("wb-gsm-sim1", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
                            sim_slot=1)

            active_cn = self.nm.get_active_connections().get("wb-eth0")
            self.tm.touch_gsm_timeout(active_cn)
            assert self.tm.deny_sim_switch_until is None

            active_cn = self.nm.get_active_connections().get("wb-gsm-sim1")
            self.tm.touch_gsm_timeout(active_cn)
            assert isinstance(self.tm.deny_sim_switch_until, datetime.datetime)
            delta = self.tm.deny_sim_switch_until - TEST_NOW
            assert delta.total_seconds() == self.tm.config.sticky_sim_period.total_seconds()

            active_cn = self.nm.get_active_connections().get("wb-eth0")
            self.tm.touch_gsm_timeout(active_cn)
            assert self.tm.deny_sim_switch_until is None

    def test_04_connection_retry_timeout_is_active__empty(self):
        with patch.object(self.tm, "now") as now_mock:
            now_mock.return_value = TEST_NOW
            self.tm.connection_retry_timeouts = {}
            assert self.tm.connection_retry_timeout_is_active("wb-eth0") is False
            assert self.tm.connection_retry_timeout_is_active("wb-gsm-sim1") is False

    def test_05_connection_retry_timeout_is_active__expired(self):
        with patch.object(self.tm, "now") as now_mock:
            now_mock.return_value = TEST_NOW
            self.tm.connection_retry_timeouts = {
                "wb-eth0": TEST_NOW - CONNECTION_ACTIVATION_RETRY_TIMEOUT
            }
            assert self.tm.connection_retry_timeout_is_active("wb-eth0") is False
            assert self.tm.connection_retry_timeout_is_active("wb-gsm-sim1") is False

    def test_06_connection_retry_timeout_is_active__armed(self):
        with patch.object(self.tm, "now") as now_mock:
            now_mock.return_value = TEST_NOW
            self.tm.connection_retry_timeouts = {
                "wb-eth0": TEST_NOW + CONNECTION_ACTIVATION_RETRY_TIMEOUT
            }
            assert self.tm.connection_retry_timeout_is_active("wb-eth0") is True
            assert self.tm.connection_retry_timeout_is_active("wb-gsm-sim1") is False

    def test_07_gsm_sticky_timeout_is_active__empty(self):
        with patch.object(self.tm, "now") as now_mock:
            now_mock.return_value = TEST_NOW
            self.tm.deny_sim_switch_until = None
            assert self.tm.gsm_sticky_timeout_is_active() is False

    def test_08_gsm_sticky_timeout_is_active__expired(self):
        with patch.object(self.tm, "now") as now_mock:
            now_mock.return_value = TEST_NOW
            self.tm.deny_sim_switch_until = TEST_NOW - self.tm.config.sticky_sim_period
            assert self.tm.gsm_sticky_timeout_is_active() is False

    def test_09_gsm_sticky_timeout_is_active__active(self):
        with patch.object(self.tm, "now") as now_mock:
            now_mock.return_value = TEST_NOW
            self.tm.deny_sim_switch_until = TEST_NOW + self.tm.config.sticky_sim_period
            assert self.tm.gsm_sticky_timeout_is_active() is True
