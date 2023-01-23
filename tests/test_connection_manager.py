import datetime
import logging
import unittest
from unittest.mock import patch, call

from tests.mm_mock import FakeNMActiveConnection, FakeNetworkManager, FakeModemManager, FakeNMConnection
from wb.nm_helper.connection_manager import (
    CONNECTION_ACTIVATION_RETRY_TIMEOUT,
    ConnectionManager,
    ConnectionManagerConfigFile,
)
from wb.nm_helper.network_manager import (
    NM_ACTIVE_CONNECTION_STATE_ACTIVATING,
    NM_ACTIVE_CONNECTION_STATE_ACTIVATED, NM_ACTIVE_CONNECTION_STATE_DEACTIVATED,
    NM_ACTIVE_CONNECTION_STATE_UNKNOWN, NMConnection, NMActiveConnection,
)

logging.basicConfig(level=logging.DEBUG)

DEFAULT_CONFIG = {
    "tiers": {
        "high": ["wb-eth0", "wb-eth1"],
        "medium": ["wb-wifi-client"],
        "low": ["wb-gsm-sim1", "wb-gsm-sim2"],
    }
}

SHORT_TIMEOUT = datetime.timedelta(seconds=5)


class ConnectionManagerCheckTests(unittest.TestCase):

    def test_01_check_simple(self):
        local_config = {
            "tiers": {
                "high": ["wb-eth0"],
                "medium": [],
                "low": [],
            }
        }
        net_man = FakeNetworkManager()
        net_man.add_ethernet("wb-eth0", device_connected=True)
        config = ConnectionManagerConfigFile(local_config)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))
        with patch.object(cm, "curl_get") as curl_get_mock:
            curl_get_mock.side_effect = [cm.config.connectivity_check_payload]
            assert cm.current_connection is None
            assert cm.current_tier is None
            assert len(cm.network_manager.get_active_connections()) == 0
            tier, con, changed = cm.check()
            assert tier == config.tiers[0]
            assert con == "wb-eth0"
            assert changed is True
            curl_get_mock.assert_has_calls([
                call("wb-eth0", cm.config.connectivity_check_url)
            ])

    def test_02_check_one_skip_disconnected(self):
        local_config = {
            "tiers": {
                "high": ["wb-eth0"],
                "medium": ["wb-eth1"],
                "low": [],
            }
        }
        net_man = FakeNetworkManager()
        net_man.add_ethernet("wb-eth0", device_connected=False)
        net_man.add_ethernet("wb-eth1", device_connected=True)
        config = ConnectionManagerConfigFile(local_config)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))
        with patch.object(cm, "curl_get") as curl_get_mock:
            curl_get_mock.side_effect = [cm.config.connectivity_check_payload]
            assert cm.current_connection is None
            assert cm.current_tier is None
            assert len(cm.network_manager.get_active_connections()) == 0
            tier, con, changed = cm.check()
            assert tier == config.tiers[1]
            assert con == "wb-eth1"
            assert changed is True
            curl_get_mock.assert_has_calls([
                call("wb-eth1", cm.config.connectivity_check_url),
            ])

    def test_03_check_one_skip_unreachable(self):
        local_config = {
            "tiers": {
                "high": ["wb-eth0"],
                "medium": ["wb-eth1"],
                "low": [],
            }
        }
        net_man = FakeNetworkManager()
        net_man.add_ethernet("wb-eth0", device_connected=True)
        net_man.add_ethernet("wb-eth1", device_connected=True)
        config = ConnectionManagerConfigFile(local_config)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))
        with patch.object(cm, "curl_get") as curl_get_mock:
            curl_get_mock.side_effect = ["", cm.config.connectivity_check_payload]
            assert cm.current_connection is None
            assert cm.current_tier is None
            assert len(cm.network_manager.get_active_connections()) == 0
            tier, con, changed = cm.check()
            assert tier == config.tiers[1]
            assert con == "wb-eth1"
            assert changed is True
            curl_get_mock.assert_has_calls([
                call("wb-eth0", cm.config.connectivity_check_url),
                call("wb-eth1", cm.config.connectivity_check_url),
            ])

    def test_04_check_wifi_ok(self):
        local_config = {
            "tiers": {
                "high": ["wb-eth0"],
                "medium": ["wb-wifi-client"],
                "low": [],
            }
        }
        net_man = FakeNetworkManager()
        net_man.add_ethernet("wb-eth0", device_connected=True)
        net_man.add_wifi_client("wb-wifi-client", device_connected=True)
        config = ConnectionManagerConfigFile(local_config)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))
        with patch.object(cm, "curl_get") as curl_get_mock:
            curl_get_mock.side_effect = ["", cm.config.connectivity_check_payload]
            assert cm.current_connection is None
            assert cm.current_tier is None
            assert len(cm.network_manager.get_active_connections()) == 0
            tier, con, changed = cm.check()
            assert tier == config.tiers[1]
            assert con == "wb-wifi-client"
            assert changed is True
            curl_get_mock.assert_has_calls([
                call("wb-eth0", cm.config.connectivity_check_url),
                call("wb-wifi-client", cm.config.connectivity_check_url),
            ])

    def test_05_check_wifi_stuck_activating(self):
        local_config = {
            "tiers": {
                "high": ["wb-eth0"],
                "medium": ["wb-wifi-client"],
                "low": ["wb-eth1"],
            }
        }
        net_man = FakeNetworkManager()
        net_man.add_ethernet("wb-eth0", device_connected=True)
        net_man.add_ethernet("wb-eth1", device_connected=True)
        net_man.add_wifi_client("wb-wifi-client", device_connected=True, should_stuck_activating=True)
        config = ConnectionManagerConfigFile(local_config)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))
        cm.timeouts.connection_activation_timeout = SHORT_TIMEOUT
        with patch.object(cm, "curl_get") as curl_get_mock:
            curl_get_mock.side_effect = ["", cm.config.connectivity_check_payload]
            assert cm.current_connection is None
            assert cm.current_tier is None
            assert len(cm.network_manager.get_active_connections()) == 0
            tier, con, changed = cm.check()
            assert tier == config.tiers[2]
            assert con == "wb-eth1"
            assert changed is True
            curl_get_mock.assert_has_calls([
                call("wb-eth0", cm.config.connectivity_check_url),
                call("wb-eth1", cm.config.connectivity_check_url),
            ])

    def test_06_check_gsm_simple(self):
        local_config = {
            "tiers": {
                "high": ["wb-eth0", "wb-eth1"],
                "medium": ["wb-wifi-client"],
                "low": ["wb-gsm-sim1"],
            }
        }
        net_man = FakeNetworkManager()
        net_man.add_ethernet("wb-eth0", device_connected=True)
        net_man.add_ethernet("wb-eth1", device_connected=True)
        net_man.add_wifi_client("wb-wifi-client", device_connected=False)
        net_man.add_gsm("wb-gsm-sim1", device_connected=True, sim_slot=1)
        config = ConnectionManagerConfigFile(local_config)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))
        cm.timeouts.connection_activation_timeout = SHORT_TIMEOUT
        with patch.object(cm, "curl_get") as curl_get_mock:
            curl_get_mock.side_effect = ["", "", cm.config.connectivity_check_payload]
            assert cm.current_connection is None
            assert cm.current_tier is None
            assert len(cm.network_manager.get_active_connections()) == 0
            tier, con, changed = cm.check()
            assert tier == config.tiers[2]
            assert con == "wb-gsm-sim1"
            assert changed is True
            curl_get_mock.assert_has_calls([
                call("wb-eth0", cm.config.connectivity_check_url),
                call("wb-eth1", cm.config.connectivity_check_url),
                call("wb-gsm-sim1", cm.config.connectivity_check_url),
            ])

    def test_07_check_gsm_change_slot_inactive(self):
        local_config = {
            "tiers": {
                "high": ["wb-eth0", "wb-eth1"],
                "medium": ["wb-wifi-client"],
                "low": ["wb-gsm-sim1", "wb-gsm-sim2"],
            }
        }
        net_man = FakeNetworkManager()
        net_man.add_ethernet("wb-eth0", device_connected=True)
        net_man.add_ethernet("wb-eth1", device_connected=True)
        net_man.add_wifi_client("wb-wifi-client", device_connected=False)
        net_man.add_gsm("wb-gsm-sim1", device_connected=False, sim_slot=1)
        net_man.add_gsm("wb-gsm-sim2", device_connected=True, sim_slot=2)
        config = ConnectionManagerConfigFile(local_config)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))
        cm.timeouts.connection_activation_timeout = SHORT_TIMEOUT
        with patch.object(cm, "curl_get") as curl_get_mock:
            curl_get_mock.side_effect = ["", "", cm.config.connectivity_check_payload]
            assert cm.current_connection is None
            assert cm.current_tier is None
            assert len(cm.network_manager.get_active_connections()) == 0
            tier, con, changed = cm.check()
            assert tier == config.tiers[2]
            assert con == "wb-gsm-sim2"
            assert changed is True
            curl_get_mock.assert_has_calls([
                call("wb-eth0", cm.config.connectivity_check_url),
                call("wb-eth1", cm.config.connectivity_check_url),
                call("wb-gsm-sim2", cm.config.connectivity_check_url),
            ])

    def test_08_check_gsm_change_slot_active(self):
        local_config = {
            "tiers": {
                "high": ["wb-eth0", "wb-eth1", "wb-gsm-sim2"],
                "medium": ["wb-wifi-client"],
                "low": ["wb-gsm-sim1"],
            }
        }
        net_man = FakeNetworkManager()
        net_man.add_ethernet("wb-eth0", device_connected=True)
        net_man.add_ethernet("wb-eth1", device_connected=True)
        net_man.add_wifi_client("wb-wifi-client", device_connected=False)
        net_man.add_gsm("wb-gsm-sim1", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
                        sim_slot=1)
        net_man.add_gsm("wb-gsm-sim2", device_connected=True, sim_slot=2)
        config = ConnectionManagerConfigFile(local_config)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))
        cm.timeouts.connection_activation_timeout = SHORT_TIMEOUT
        with patch.object(cm, "curl_get") as curl_get_mock:
            curl_get_mock.side_effect = ["", "", cm.config.connectivity_check_payload]
            cm.current_connection = "wb-gsm-sim1"
            cm.current_tier = config.tiers[2]
            assert len(cm.network_manager.get_active_connections()) == 1
            tier, con, changed = cm.check()
            assert tier == config.tiers[0]
            assert con == "wb-gsm-sim2"
            assert changed is True
            curl_get_mock.assert_has_calls([
                call("wb-eth0", cm.config.connectivity_check_url),
                call("wb-eth1", cm.config.connectivity_check_url),
                call("wb-gsm-sim2", cm.config.connectivity_check_url),
            ])


class ConnectionManagerTests(unittest.TestCase):

    def _is_active_connection(self, con):
        return isinstance(con, NMActiveConnection) or isinstance(con, FakeNMActiveConnection)

    def _is_connection(self, con):
        return isinstance(con, NMConnection) or isinstance(con, FakeNMConnection)

    def test_02_ok_to_activate(self):
        config = ConnectionManagerConfigFile(DEFAULT_CONFIG)
        net_man = FakeNetworkManager()
        net_man.add_ethernet("wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED)
        net_man.add_gsm("wb-gsm-sim1", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED, sim_slot=1)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))

        cm.timeouts.connection_retry_timeouts = {}
        cm.timeouts.deny_sim_switch_until = None
        assert cm.ok_to_activate_connection("wb-eth0") is True
        assert cm.ok_to_activate_connection("wb-gsm-sim1") is True

        cm.timeouts.connection_retry_timeouts = {
            "wb-eth0": datetime.datetime.now() - CONNECTION_ACTIVATION_RETRY_TIMEOUT,
            "wb-gsm-sim1": datetime.datetime.now() - CONNECTION_ACTIVATION_RETRY_TIMEOUT,
        }
        assert cm.ok_to_activate_connection("wb-eth0") is True
        assert cm.ok_to_activate_connection("wb-gsm-sim1") is True

        cm.timeouts.connection_retry_timeouts = {
            "wb-eth0": datetime.datetime.now() + CONNECTION_ACTIVATION_RETRY_TIMEOUT,
            "wb-gsm-sim1": datetime.datetime.now() + CONNECTION_ACTIVATION_RETRY_TIMEOUT,
        }
        assert cm.ok_to_activate_connection("wb-eth0") is False
        assert cm.ok_to_activate_connection("wb-gsm-sim1") is False

        cm.timeouts.connection_retry_timeouts = {}
        cm.timeouts.deny_sim_switch_until = datetime.datetime.now() + cm.config.sticky_sim_period
        assert cm.ok_to_activate_connection("wb-gsm-sim1") is False

        cm.timeouts.connection_retry_timeouts = {
            "wb-gsm-sim1": datetime.datetime.now() - CONNECTION_ACTIVATION_RETRY_TIMEOUT
        }
        cm.timeouts.deny_sim_switch_until = datetime.datetime.now() + cm.config.sticky_sim_period
        assert cm.ok_to_activate_connection("wb-gsm-sim1") is False

        cm.timeouts.connection_retry_timeouts = {
            "wb-gsm-sim1": datetime.datetime.now() + CONNECTION_ACTIVATION_RETRY_TIMEOUT
        }
        cm.timeouts.deny_sim_switch_until = datetime.datetime.now() + cm.config.sticky_sim_period
        assert cm.ok_to_activate_connection("wb-gsm-sim1") is False

    def test_03_get_active_connection_1(self):

        net_man = FakeNetworkManager()
        net_man.add_ethernet("wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED)
        net_man.add_ethernet("wb-eth1", device_connected=True)
        net_man.add_gsm("wb-gsm-sim1", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
                        sim_slot=1)
        net_man.add_gsm("wb-gsm-sim2", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN,
                        sim_slot=2)

        config = ConnectionManagerConfigFile(DEFAULT_CONFIG)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))

        r1 = cm.get_active_connection("wb-eth0")
        r2 = cm.get_active_connection("wb-eth1")
        r3 = cm.get_active_connection("wb-gsm-sim1")
        r4 = cm.get_active_connection("wb-gsm-sim2")

        assert isinstance(r1, NMActiveConnection) or isinstance(r1, FakeNMActiveConnection)
        assert r1.get_connection_id() == "wb-eth0"
        assert r2 is None
        assert isinstance(r3, NMActiveConnection) or isinstance(r3, FakeNMActiveConnection)
        assert r3.get_connection_id() == "wb-gsm-sim1"
        assert r4 is None

    def test_04_get_active_connection_2(self):

        net_man = FakeNetworkManager()
        net_man.add_ethernet("wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED)
        net_man.add_ethernet("wb-eth1", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATING)

        config = ConnectionManagerConfigFile(DEFAULT_CONFIG)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))

        r1 = cm.get_active_connection("wb-eth0", require_activated=True)
        r2 = cm.get_active_connection("wb-eth1", require_activated=True)

        assert self._is_active_connection(r1)
        assert r1.get_connection_id() == "wb-eth0"
        assert r2 is None

    def test_05_activate_connection_eth(self):

        net_man = FakeNetworkManager()
        net_man.add_ethernet("wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN)

        config = ConnectionManagerConfigFile(DEFAULT_CONFIG)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))

        con = cm.get_active_connection("wb-eth0")
        assert con is None
        cm.activate_connection("wb-eth0")
        con = cm.get_active_connection("wb-eth0", require_activated=True)
        assert self._is_active_connection(con)
        assert con.get_connection_id() == "wb-eth0"

    def test_06_activate_connection_wifi(self):
        net_man = FakeNetworkManager()
        net_man.add_wifi_client("wb-wifi-client", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN)
        config = ConnectionManagerConfigFile(DEFAULT_CONFIG)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))

        con = cm.get_active_connection("wb-wifi-client")
        assert con is None
        cm.activate_connection("wb-wifi-client")
        con = cm.get_active_connection("wb-wifi-client", require_activated=True)
        assert self._is_active_connection(con)
        assert con.get_connection_id() == "wb-wifi-client"

    def test_07_activate_connection_gsm(self):
        net_man = FakeNetworkManager()
        net_man.add_gsm("wb-gsm-sim1", device_connected=True, state=NM_ACTIVE_CONNECTION_STATE_DEACTIVATED, sim_slot=1)
        config = ConnectionManagerConfigFile(DEFAULT_CONFIG)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))

        con = cm.get_active_connection("wb-gsm-sim1")
        assert con is None
        cm.activate_connection("wb-gsm-sim1")
        con = cm.get_active_connection("wb-gsm-sim1", require_activated=True)
        assert self._is_active_connection(con)
        assert con.get_connection_id() == "wb-gsm-sim1"

    def test_08_deactivate_connection_eth(self):
        net_man = FakeNetworkManager()
        net_man.add_ethernet("wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN)
        config = ConnectionManagerConfigFile(DEFAULT_CONFIG)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))

        con = cm.get_active_connection("wb-eth0")
        assert con is None
        cm.activate_connection("wb-eth0")
        con = cm.get_active_connection("wb-eth0", require_activated=True)
        assert self._is_active_connection(con)
        assert con.get_connection_id() == "wb-eth0"
        cm.deactivate_connection(con)
        con = cm.get_active_connection("wb-eth0")
        assert con is None

    def test_09_deactivate_connection_wifi(self):
        net_man = FakeNetworkManager()
        net_man.add_wifi_client("wb-wifi-client", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN)
        config = ConnectionManagerConfigFile(DEFAULT_CONFIG)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))

        con = cm.get_active_connection("wb-wifi-client")
        assert con is None
        cm.activate_connection("wb-wifi-client")
        con = cm.get_active_connection("wb-wifi-client", require_activated=True)
        assert self._is_active_connection(con)
        assert con.get_connection_id() == "wb-wifi-client"
        cm.deactivate_connection(con)
        con = cm.get_active_connection("wb-wifi-client")
        assert con is None

    def test_10_deactivate_connection_gsm(self):
        net_man = FakeNetworkManager()
        net_man.add_gsm("wb-gsm-sim1", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED, sim_slot=1)
        config = ConnectionManagerConfigFile(DEFAULT_CONFIG)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))

        con = cm.get_active_connection("wb-gsm-sim1")
        assert self._is_active_connection(con)
        assert con.get_connection_id() == "wb-gsm-sim1"
        cm.deactivate_connection(con)
        con = cm.get_active_connection("wb-gsm-sim1")
        assert con is None

    def test_11_get_sim_slot(self):
        net_man = FakeNetworkManager()
        net_man.add_gsm("wb-gsm-sim1", sim_slot=1)
        net_man.add_gsm("wb-gsm-sim2", sim_slot=2)
        config = ConnectionManagerConfigFile(DEFAULT_CONFIG)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))

        con = cm.find_connection("wb-gsm-sim1")
        assert self._is_connection(con)
        val = cm.get_sim_slot(con)
        assert val == 1

        con = cm.find_connection("wb-gsm-sim2")
        assert self._is_connection(con)
        val = cm.get_sim_slot(con)
        assert val == 2

    def test_12_connection_is_gsm(self):
        net_man = FakeNetworkManager()
        net_man.add_gsm("wb-gsm-sim1")
        net_man.add_ethernet("wb-eth0")
        config = ConnectionManagerConfigFile(DEFAULT_CONFIG)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))

        assert cm.connection_is_gsm("eth0") is False
        assert cm.connection_is_gsm("wb-gsm-sim1") is True
        assert cm.connection_is_gsm("non-entity") is False

    def test_13_set_current_connection(self):
        net_man = FakeNetworkManager()
        net_man.add_gsm("wb-gsm-sim1", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED)
        net_man.add_ethernet("wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED)
        config = ConnectionManagerConfigFile(DEFAULT_CONFIG)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))
        cm.current_tier = config.tiers[0]
        cm.current_connection = "wb-eth1"

        active_cn = cm.network_manager.get_active_connections().get("wb-eth0")
        new_tier, new_cn_id, changed = cm.set_current_connection(active_cn, "wb-eth0", config.tiers[0])
        assert new_tier == config.tiers[0]
        assert new_cn_id == "wb-eth0"
        assert changed is True
        assert cm.timeouts.deny_sim_switch_until is None

        active_cn = cm.network_manager.get_active_connections().get("wb-eth0")
        new_tier, new_cn_id, changed = cm.set_current_connection(active_cn, "wb-eth0", config.tiers[0])
        assert new_tier == config.tiers[0]
        assert new_cn_id == "wb-eth0"
        assert changed is False
        assert cm.timeouts.deny_sim_switch_until is None

        active_cn = cm.network_manager.get_active_connections().get("wb-gsm-sim1")
        new_tier, new_cn_id, changed = cm.set_current_connection(active_cn, "wb-gsm-sim1", config.tiers[2])
        assert new_tier == config.tiers[2]
        assert new_cn_id == "wb-gsm-sim1"
        assert changed is True
        assert isinstance(cm.timeouts.deny_sim_switch_until, datetime.datetime)
        delta = cm.timeouts.deny_sim_switch_until - datetime.datetime.now()
        high_limit = (config.sticky_sim_period.total_seconds() + 5)
        low_limit = (config.sticky_sim_period.total_seconds() - 5)
        assert high_limit > delta.total_seconds() > low_limit

        active_cn = cm.network_manager.get_active_connections().get("wb-gsm-sim1")
        new_tier, new_cn_id, changed = cm.set_current_connection(active_cn, "wb-gsm-sim1", config.tiers[2])
        assert new_tier == config.tiers[2]
        assert new_cn_id == "wb-gsm-sim1"
        assert changed is False
        assert isinstance(cm.timeouts.deny_sim_switch_until, datetime.datetime)
        delta = cm.timeouts.deny_sim_switch_until - datetime.datetime.now()
        high_limit = (config.sticky_sim_period.total_seconds() + 5)
        low_limit = (config.sticky_sim_period.total_seconds() - 5)
        assert high_limit > delta.total_seconds() > low_limit

        active_cn = cm.network_manager.get_active_connections().get("wb-eth0")
        new_tier, new_cn_id, changed = cm.set_current_connection(active_cn, "wb-eth0", config.tiers[0])
        assert new_tier == config.tiers[0]
        assert new_cn_id == "wb-eth0"
        assert changed is True
        assert cm.timeouts.deny_sim_switch_until is None

    def test_14_find_lesser_gsm_connections(self):
        config_data = {
            "tiers": {
                "high": ["wb-gsm-sim1", "wb-eth1"],
                "normal": ["wb-wifi-client"],
                "low": ["wb-eth0", "wb-gsm-sim2"],
            }
        }
        net_man = FakeNetworkManager()
        net_man.add_ethernet("wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED)
        net_man.add_ethernet("wb-eth1", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN)
        net_man.add_gsm("wb-gsm-sim1", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
                        sim_slot=1)
        net_man.add_gsm("wb-gsm-sim2", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
                        sim_slot=2)
        config = ConnectionManagerConfigFile(config_data)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))

        lesser = list(cm.find_lesser_gsm_connections("wb-gsm-sim1", config.tiers[0]))
        assert len(lesser) == 1
        assert lesser[0].get_connection_id() == "wb-gsm-sim2"

        lesser = list(cm.find_lesser_gsm_connections("wb-eth1", config.tiers[0]))
        assert len(lesser) == 2
        assert lesser[0].get_connection_id() == "wb-gsm-sim1"
        assert lesser[1].get_connection_id() == "wb-gsm-sim2"

        lesser = list(cm.find_lesser_gsm_connections("wb-gsm-sim2", config.tiers[2]))
        assert len(lesser) == 0

    def test_15_deactivate_lesser_gsm_connections_1(self):
        config_data = {"tiers": {"high": ["wb-gsm-sim1"], "normal": ["wb-eth0"], "low": ["wb-gsm-sim2"]}}

        net_man = FakeNetworkManager()
        net_man.add_gsm("wb-gsm-sim1", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
                        sim_slot=1)
        net_man.add_gsm("wb-gsm-sim2", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
                        sim_slot=2)
        config = ConnectionManagerConfigFile(config_data)
        cm = ConnectionManager(net_man, config, modem_manager=FakeModemManager(net_man))
        cm.current_connection = "wb-eth0"
        cm.current_tier = config.tiers[1]

        assert self._is_active_connection(cm.get_active_connection("wb-gsm-sim1"))
        assert self._is_active_connection(cm.get_active_connection("wb-gsm-sim2"))

        cm.deactivate_lesser_gsm_connections(cm.current_connection, cm.current_tier)

        assert self._is_active_connection(cm.get_active_connection("wb-gsm-sim1"))
        assert cm.get_active_connection("wb-gsm-sim2") is None
