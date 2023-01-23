import datetime
import unittest

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


class TimeoutManagerTests(unittest.TestCase):
    def test_touch_connection_retry_timeout(self):

        config = ConnectionManagerConfigFile(normal_config)
        tm = TimeoutManager(config)
        tm.touch_connection_retry_timeout("wb-eth0")
        timeout = CONNECTION_ACTIVATION_RETRY_TIMEOUT.total_seconds()
        assert type(tm.connection_retry_timeouts.get("wb-eth0")) is datetime.datetime
        delta = tm.connection_retry_timeouts.get("wb-eth0") - datetime.datetime.now()
        assert timeout >= delta.total_seconds() >= (timeout - 5)

    def test_reset_connection_retry_timeout(self):

        config = ConnectionManagerConfigFile(normal_config)
        tm = TimeoutManager(config)
        tm.touch_connection_retry_timeout("wb-eth0")
        tm.reset_connection_retry_timeout("wb-eth0")
        assert type(tm.connection_retry_timeouts.get("wb-eth0")) is datetime.datetime
        delta = tm.connection_retry_timeouts.get("wb-eth0") - datetime.datetime.now()
        assert delta.total_seconds() <= 0

    def test_touch_gsm_timeout(self):

        config = ConnectionManagerConfigFile(normal_config)
        tm = TimeoutManager(config)
        nm = FakeNetworkManager()
        nm.add_ethernet("wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED)
        nm.add_gsm("wb-gsm-sim1", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED, sim_slot=1)

        active_cn = nm.get_active_connections().get("wb-eth0")
        tm.touch_gsm_timeout(active_cn)
        assert tm.deny_sim_switch_until is None

        active_cn = nm.get_active_connections().get("wb-gsm-sim1")
        tm.touch_gsm_timeout(active_cn)
        assert type(tm.deny_sim_switch_until) is datetime.datetime
        delta = tm.deny_sim_switch_until - datetime.datetime.now()
        assert 901 > delta.total_seconds() > 890

        active_cn = nm.get_active_connections().get("wb-eth0")
        tm.touch_gsm_timeout(active_cn)
        assert tm.deny_sim_switch_until is None

    def test_connection_retry_timeout_is_active(self):

        config = ConnectionManagerConfigFile({})
        tm = TimeoutManager(config)

        tm.connection_retry_timeouts = {}
        assert tm.connection_retry_timeout_is_active("wb-eth0") is False
        assert tm.connection_retry_timeout_is_active("wb-gsm-sim1") is False

        tm.connection_retry_timeouts = {
            "wb-eth0": datetime.datetime.now() - CONNECTION_ACTIVATION_RETRY_TIMEOUT
        }
        assert tm.connection_retry_timeout_is_active("wb-eth0") is False
        assert tm.connection_retry_timeout_is_active("wb-gsm-sim1") is False

        tm.connection_retry_timeouts = {
            "wb-eth0": datetime.datetime.now() + CONNECTION_ACTIVATION_RETRY_TIMEOUT
        }
        assert tm.connection_retry_timeout_is_active("wb-eth0") is True
        assert tm.connection_retry_timeout_is_active("wb-gsm-sim1") is False

    def test_gsm_sticky_timeout_is_active(self):

        config = ConnectionManagerConfigFile(normal_config)
        tm = TimeoutManager(config)

        tm.deny_sim_switch_until = None
        assert tm.gsm_sticky_timeout_is_active() is False

        tm.deny_sim_switch_until = datetime.datetime.now() + tm.config.sticky_sim_period
        assert tm.gsm_sticky_timeout_is_active() is True
