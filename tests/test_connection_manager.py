import datetime
import logging
import unittest
from unittest.mock import call, patch

from tests.mm_mock import (
    FakeModemManager,
    FakeNetworkManager,
    FakeNMActiveConnection,
    FakeNMConnection,
)
from wb.nm_helper.connection_manager import (
    CONNECTION_ACTIVATION_RETRY_TIMEOUT,
    ConnectionManager,
    ConnectionManagerConfigFile,
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


class AbsConManCheckTests(unittest.TestCase):
    def _init_con_man(self, config_data):
        self.config = ConnectionManagerConfigFile(config_data)
        self.net_man = FakeNetworkManager()
        self.mod_man = FakeModemManager(self.net_man)
        self.con_man = ConnectionManager(self.net_man, self.config, modem_manager=self.mod_man)
        self.con_man.timeouts.connection_activation_timeout = SHORT_TIMEOUT


class ConnectionManagerCheckTests(AbsConManCheckTests):
    def test_01_check_simple(self):
        local_config = {
            "tiers": {
                "high": ["wb-eth0"],
                "medium": [],
                "low": [],
            }
        }
        self._init_con_man(local_config)
        self.net_man.add_ethernet("wb-eth0", device_connected=True)
        with patch.object(self.con_man, "curl_get") as curl_get_mock:
            curl_get_mock.side_effect = [self.con_man.config.connectivity_check_payload]
            assert self.con_man.current_connection is None
            assert self.con_man.current_tier is None
            assert len(self.con_man.network_manager.get_active_connections()) == 0
            tier, con, changed = self.con_man.check()
            assert tier == self.config.tiers[0]
            assert con == "wb-eth0"
            assert changed is True
            curl_get_mock.assert_has_calls([call("if_wb-eth0", self.con_man.config.connectivity_check_url)])

    def test_02_check_one_skip_disconnected(self):
        local_config = {
            "tiers": {
                "high": ["wb-eth0"],
                "medium": ["wb-eth1"],
                "low": [],
            }
        }
        self._init_con_man(local_config)
        self.net_man.add_ethernet("wb-eth0", device_connected=False)
        self.net_man.add_ethernet("wb-eth1", device_connected=True)
        with patch.object(self.con_man, "curl_get") as curl_get_mock:
            curl_get_mock.side_effect = [self.con_man.config.connectivity_check_payload]
            assert self.con_man.current_connection is None
            assert self.con_man.current_tier is None
            assert len(self.con_man.network_manager.get_active_connections()) == 0
            tier, con, changed = self.con_man.check()
            assert tier == self.config.tiers[1]
            assert con == "wb-eth1"
            assert changed is True
            curl_get_mock.assert_has_calls(
                [
                    call("if_wb-eth1", self.con_man.config.connectivity_check_url),
                ]
            )

    def test_03_check_one_skip_unreachable(self):
        local_config = {
            "tiers": {
                "high": ["wb-eth0"],
                "medium": ["wb-eth1"],
                "low": [],
            }
        }
        self._init_con_man(local_config)
        self.net_man.add_ethernet("wb-eth0", device_connected=True)
        self.net_man.add_ethernet("wb-eth1", device_connected=True)
        with patch.object(self.con_man, "curl_get") as curl_get_mock:
            curl_get_mock.side_effect = ["", self.con_man.config.connectivity_check_payload]
            assert self.con_man.current_connection is None
            assert self.con_man.current_tier is None
            assert len(self.con_man.network_manager.get_active_connections()) == 0
            tier, con, changed = self.con_man.check()
            assert tier == self.config.tiers[1]
            assert con == "wb-eth1"
            assert changed is True
            curl_get_mock.assert_has_calls(
                [
                    call("if_wb-eth0", self.con_man.config.connectivity_check_url),
                    call("if_wb-eth1", self.con_man.config.connectivity_check_url),
                ]
            )

    def test_04_check_wifi_ok(self):
        local_config = {
            "tiers": {
                "high": ["wb-eth0"],
                "medium": ["wb-wifi-client"],
                "low": [],
            }
        }
        self._init_con_man(local_config)
        self.net_man.add_ethernet("wb-eth0", device_connected=True)
        self.net_man.add_wifi_client("wb-wifi-client", device_connected=True)
        with patch.object(self.con_man, "curl_get") as curl_get_mock:
            curl_get_mock.side_effect = ["", self.con_man.config.connectivity_check_payload]
            assert self.con_man.current_connection is None
            assert self.con_man.current_tier is None
            assert len(self.con_man.network_manager.get_active_connections()) == 0
            tier, con, changed = self.con_man.check()
            assert tier == self.config.tiers[1]
            assert con == "wb-wifi-client"
            assert changed is True
            curl_get_mock.assert_has_calls(
                [
                    call("if_wb-eth0", self.con_man.config.connectivity_check_url),
                    call("if_wb-wifi-client", self.con_man.config.connectivity_check_url),
                ]
            )

    def test_05_check_wifi_stuck_activating(self):
        local_config = {
            "tiers": {
                "high": ["wb-eth0"],
                "medium": ["wb-wifi-client"],
                "low": ["wb-eth1"],
            }
        }
        self._init_con_man(local_config)
        self.net_man.add_ethernet("wb-eth0", device_connected=True)
        self.net_man.add_ethernet("wb-eth1", device_connected=True)
        self.net_man.add_wifi_client("wb-wifi-client", device_connected=True, should_stuck_activating=True)
        with patch.object(self.con_man, "curl_get") as curl_get_mock:
            curl_get_mock.side_effect = ["", self.con_man.config.connectivity_check_payload]
            assert self.con_man.current_connection is None
            assert self.con_man.current_tier is None
            assert len(self.con_man.network_manager.get_active_connections()) == 0
            tier, con, changed = self.con_man.check()
            assert tier == self.config.tiers[2]
            assert con == "wb-eth1"
            assert changed is True
            curl_get_mock.assert_has_calls(
                [
                    call("if_wb-eth0", self.con_man.config.connectivity_check_url),
                    call("if_wb-eth1", self.con_man.config.connectivity_check_url),
                ]
            )

    def test_06_check_gsm_simple(self):
        local_config = {
            "tiers": {
                "high": ["wb-eth0", "wb-eth1"],
                "medium": ["wb-wifi-client"],
                "low": ["wb-gsm-sim1"],
            }
        }
        self._init_con_man(local_config)
        self.net_man.add_ethernet("wb-eth0", device_connected=True)
        self.net_man.add_ethernet("wb-eth1", device_connected=True)
        self.net_man.add_wifi_client("wb-wifi-client", device_connected=False)
        self.net_man.add_gsm("wb-gsm-sim1", device_connected=True, sim_slot=1)
        with patch.object(self.con_man, "curl_get") as curl_get_mock:
            with patch.object(self.con_man, "call_ifmetric") as call_ifmetric_mock:
                curl_get_mock.side_effect = ["", "", self.con_man.config.connectivity_check_payload]
                assert self.con_man.current_connection is None
                assert self.con_man.current_tier is None
                assert len(self.con_man.network_manager.get_active_connections()) == 0
                tier, con, changed = self.con_man.check()
                curl_get_mock.assert_has_calls(
                    [
                        call("if_wb-eth0", self.con_man.config.connectivity_check_url),
                        call("if_wb-eth1", self.con_man.config.connectivity_check_url),
                        call("ppp0", self.con_man.config.connectivity_check_url),
                    ]
                )
                call_ifmetric_mock.assert_has_calls(
                    [
                        call("ppp0", 55),
                    ]
                )
                assert tier == self.config.tiers[2]
                assert con == "wb-gsm-sim1"
                assert changed is True


    def test_07_check_gsm_change_slot_inactive(self):
        local_config = {
            "tiers": {
                "high": ["wb-eth0", "wb-eth1"],
                "medium": ["wb-wifi-client"],
                "low": ["wb-gsm-sim1", "wb-gsm-sim2"],
            }
        }
        self._init_con_man(local_config)
        self.net_man.add_ethernet("wb-eth0", device_connected=True)
        self.net_man.add_ethernet("wb-eth1", device_connected=True)
        self.net_man.add_wifi_client("wb-wifi-client", device_connected=False)
        self.net_man.add_gsm("wb-gsm-sim1", device_connected=False, sim_slot=1)
        self.net_man.add_gsm("wb-gsm-sim2", device_connected=True, sim_slot=2)
        with patch.object(self.con_man, "curl_get") as curl_get_mock:
            with patch.object(self.con_man, "call_ifmetric") as call_ifmetric_mock:
                curl_get_mock.side_effect = ["", "", self.con_man.config.connectivity_check_payload]
                assert self.con_man.current_connection is None
                assert self.con_man.current_tier is None
                assert len(self.con_man.network_manager.get_active_connections()) == 0
                tier, con, changed = self.con_man.check()
                curl_get_mock.assert_has_calls(
                    [
                        call("if_wb-eth0", self.con_man.config.connectivity_check_url),
                        call("if_wb-eth1", self.con_man.config.connectivity_check_url),
                        call("ppp0", self.con_man.config.connectivity_check_url),
                    ]
                )
                call_ifmetric_mock.assert_has_calls(
                    [
                        call("ppp0", 55),
                    ]
                )
                assert changed is True
                assert con == "wb-gsm-sim2"
                assert tier == self.config.tiers[2]


    def test_08_check_gsm_change_slot_active(self):
        local_config = {
            "tiers": {
                "high": ["wb-eth0", "wb-eth1", "wb-gsm-sim2"],
                "medium": ["wb-wifi-client"],
                "low": ["wb-gsm-sim1"],
            }
        }
        self._init_con_man(local_config)
        self.net_man.add_ethernet("wb-eth0", device_connected=True)
        self.net_man.add_ethernet("wb-eth1", device_connected=True)
        self.net_man.add_wifi_client("wb-wifi-client", device_connected=False)
        self.net_man.add_gsm(
            "wb-gsm-sim1",
            device_connected=True,
            connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
            sim_slot=1,
        )
        self.net_man.add_gsm("wb-gsm-sim2", device_connected=True, sim_slot=2)
        with patch.object(self.con_man, "curl_get") as curl_get_mock:
            with patch.object(self.con_man, "call_ifmetric") as call_ifmetric_mock:
                curl_get_mock.side_effect = ["", "", self.con_man.config.connectivity_check_payload]
                self.con_man.current_connection = "wb-gsm-sim1"
                self.con_man.current_tier = self.config.tiers[2]
                assert len(self.con_man.network_manager.get_active_connections()) == 1
                tier, con, changed = self.con_man.check()
                assert changed is True
                assert con == "wb-gsm-sim2"
                assert tier == self.config.tiers[0]
                curl_get_mock.assert_has_calls(
                    [
                        call("if_wb-eth0", self.con_man.config.connectivity_check_url),
                        call("if_wb-eth1", self.con_man.config.connectivity_check_url),
                        call("ppp0", self.con_man.config.connectivity_check_url),
                    ]
                )
                call_ifmetric_mock.assert_has_calls(
                    [
                        call("ppp0", 55),
                    ]
                )


class ConnectionManagerTests(AbsConManCheckTests):
    def _is_active_connection(self, con):
        return isinstance(con, NMActiveConnection) or isinstance(con, FakeNMActiveConnection)

    def _is_connection(self, con):
        return isinstance(con, NMConnection) or isinstance(con, FakeNMConnection)

    def test_02_ok_to_activate(self):
        self._init_con_man(DEFAULT_CONFIG)
        self.net_man.add_ethernet(
            "wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        )
        self.net_man.add_gsm(
            "wb-gsm-sim1",
            device_connected=True,
            connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
            sim_slot=1,
        )

        with patch.object(self.con_man.timeouts, "now") as now_mock:
            now_mock.return_value = TEST_NOW

            self.con_man.timeouts.connection_retry_timeouts = {}
            self.con_man.timeouts.deny_sim_switch_until = None
            assert self.con_man.ok_to_activate_connection("wb-eth0") is True
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
            self.con_man.timeouts.deny_sim_switch_until = TEST_NOW + self.con_man.config.sticky_sim_period
            assert self.con_man.ok_to_activate_connection("wb-gsm-sim1") is False

            self.con_man.timeouts.connection_retry_timeouts = {
                "wb-gsm-sim1": TEST_NOW - CONNECTION_ACTIVATION_RETRY_TIMEOUT
            }
            self.con_man.timeouts.deny_sim_switch_until = TEST_NOW + self.con_man.config.sticky_sim_period
            assert self.con_man.ok_to_activate_connection("wb-gsm-sim1") is False

            self.con_man.timeouts.connection_retry_timeouts = {
                "wb-gsm-sim1": TEST_NOW + CONNECTION_ACTIVATION_RETRY_TIMEOUT
            }
            self.con_man.timeouts.deny_sim_switch_until = TEST_NOW + self.con_man.config.sticky_sim_period
            assert self.con_man.ok_to_activate_connection("wb-gsm-sim1") is False

    def test_03_get_active_connection_1(self):
        self._init_con_man(DEFAULT_CONFIG)
        self.net_man.add_ethernet(
            "wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        )
        self.net_man.add_ethernet("wb-eth1", device_connected=True)
        self.net_man.add_gsm(
            "wb-gsm-sim1",
            device_connected=True,
            connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
            sim_slot=1,
        )
        self.net_man.add_gsm(
            "wb-gsm-sim2",
            device_connected=True,
            connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN,
            sim_slot=2,
        )

        r1 = self.con_man.get_active_connection("wb-eth0")
        r2 = self.con_man.get_active_connection("wb-eth1")
        r3 = self.con_man.get_active_connection("wb-gsm-sim1")
        r4 = self.con_man.get_active_connection("wb-gsm-sim2")

        assert self._is_active_connection(r1)
        assert r1.get_connection_id() == "wb-eth0"
        assert r2 is None
        assert self._is_active_connection(r3)
        assert r3.get_connection_id() == "wb-gsm-sim1"
        assert r4 is None

    def test_04_get_active_connection_2(self):
        self._init_con_man(DEFAULT_CONFIG)
        self.net_man.add_ethernet(
            "wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        )
        self.net_man.add_ethernet(
            "wb-eth1", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATING
        )

        r1 = self.con_man.get_active_connection("wb-eth0", require_activated=True)
        r2 = self.con_man.get_active_connection("wb-eth1", require_activated=True)
        assert self._is_active_connection(r1)
        assert r1.get_connection_id() == "wb-eth0"
        assert r2 is None

    def test_05_activate_connection_eth(self):
        self._init_con_man(DEFAULT_CONFIG)
        self.net_man.add_ethernet(
            "wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN
        )

        con = self.con_man.get_active_connection("wb-eth0")
        assert con is None
        self.con_man.activate_connection("wb-eth0")
        con = self.con_man.get_active_connection("wb-eth0", require_activated=True)
        assert self._is_active_connection(con)
        assert con.get_connection_id() == "wb-eth0"

    def test_06_activate_connection_wifi(self):
        self._init_con_man(DEFAULT_CONFIG)
        self.net_man.add_wifi_client(
            "wb-wifi-client", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN
        )

        con = self.con_man.get_active_connection("wb-wifi-client")
        assert con is None
        self.con_man.activate_connection("wb-wifi-client")
        con = self.con_man.get_active_connection("wb-wifi-client", require_activated=True)
        assert self._is_active_connection(con)
        assert con.get_connection_id() == "wb-wifi-client"

    def test_07_activate_connection_gsm(self):
        self._init_con_man(DEFAULT_CONFIG)
        self.net_man.add_gsm(
            "wb-gsm-sim1", device_connected=True, state=NM_ACTIVE_CONNECTION_STATE_DEACTIVATED, sim_slot=1
        )

        con = self.con_man.get_active_connection("wb-gsm-sim1")
        assert con is None
        self.con_man.activate_connection("wb-gsm-sim1")
        con = self.con_man.get_active_connection("wb-gsm-sim1", require_activated=True)
        assert self._is_active_connection(con)
        assert con.get_connection_id() == "wb-gsm-sim1"

    def test_08_deactivate_connection_eth(self):
        self._init_con_man(DEFAULT_CONFIG)
        self.net_man.add_ethernet(
            "wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN
        )

        con = self.con_man.get_active_connection("wb-eth0")
        assert con is None
        self.con_man.activate_connection("wb-eth0")
        con = self.con_man.get_active_connection("wb-eth0", require_activated=True)
        assert self._is_active_connection(con)
        assert con.get_connection_id() == "wb-eth0"
        self.con_man.deactivate_connection(con)
        con = self.con_man.get_active_connection("wb-eth0")
        assert con is None

    def test_09_deactivate_connection_wifi(self):
        self._init_con_man(DEFAULT_CONFIG)
        self.net_man.add_wifi_client(
            "wb-wifi-client", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN
        )

        con = self.con_man.get_active_connection("wb-wifi-client")
        assert con is None
        self.con_man.activate_connection("wb-wifi-client")
        con = self.con_man.get_active_connection("wb-wifi-client", require_activated=True)
        assert self._is_active_connection(con)
        assert con.get_connection_id() == "wb-wifi-client"
        self.con_man.deactivate_connection(con)
        con = self.con_man.get_active_connection("wb-wifi-client")
        assert con is None

    def test_10_deactivate_connection_gsm(self):
        self._init_con_man(DEFAULT_CONFIG)
        self.net_man.add_gsm(
            "wb-gsm-sim1",
            device_connected=True,
            connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
            sim_slot=1,
        )

        con = self.con_man.get_active_connection("wb-gsm-sim1")
        assert self._is_active_connection(con)
        assert con.get_connection_id() == "wb-gsm-sim1"
        self.con_man.deactivate_connection(con)
        con = self.con_man.get_active_connection("wb-gsm-sim1")
        assert con is None

    def test_11_get_sim_slot(self):
        self._init_con_man(DEFAULT_CONFIG)
        self.net_man.add_gsm("wb-gsm-sim1", sim_slot=1)
        self.net_man.add_gsm("wb-gsm-sim2", sim_slot=2)

        con = self.con_man.find_connection("wb-gsm-sim1")
        assert self._is_connection(con)
        val = self.con_man.get_sim_slot(con)
        assert val == 1

        con = self.con_man.find_connection("wb-gsm-sim2")
        assert self._is_connection(con)
        val = self.con_man.get_sim_slot(con)
        assert val == 2

    def test_12_connection_is_gsm(self):
        self._init_con_man(DEFAULT_CONFIG)
        self.net_man.add_gsm("wb-gsm-sim1")
        self.net_man.add_ethernet("wb-eth0")

        assert self.con_man.connection_is_gsm("eth0") is False
        assert self.con_man.connection_is_gsm("wb-gsm-sim1") is True
        assert self.con_man.connection_is_gsm("non-entity") is False

    def test_13_set_current_connection(self):
        self._init_con_man(DEFAULT_CONFIG)
        self.net_man.add_gsm(
            "wb-gsm-sim1", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        )
        self.net_man.add_ethernet(
            "wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        )

        with patch.object(self.con_man.timeouts, "now") as now_mock:
            with patch.object(self.con_man, "call_ifmetric") as call_ifmetric_mock:
                now_mock.return_value = TEST_NOW

                self.con_man.current_tier = self.config.tiers[0]
                self.con_man.current_connection = "wb-eth1"

                active_cn = self.con_man.network_manager.get_active_connections().get("wb-eth0")
                new_tier, new_cn_id, changed = self.con_man.set_current_connection(
                    active_cn, "wb-eth0", self.config.tiers[0]
                )
                assert new_tier == self.config.tiers[0]
                assert new_cn_id == "wb-eth0"
                assert changed is True
                assert self.con_man.timeouts.deny_sim_switch_until is None

                active_cn = self.con_man.network_manager.get_active_connections().get("wb-eth0")
                new_tier, new_cn_id, changed = self.con_man.set_current_connection(
                    active_cn, "wb-eth0", self.config.tiers[0]
                )
                assert new_tier == self.config.tiers[0]
                assert new_cn_id == "wb-eth0"
                assert changed is False
                assert self.con_man.timeouts.deny_sim_switch_until is None

                active_cn = self.con_man.network_manager.get_active_connections().get("wb-gsm-sim1")
                new_tier, new_cn_id, changed = self.con_man.set_current_connection(
                    active_cn, "wb-gsm-sim1", self.config.tiers[2]
                )
                assert new_tier == self.config.tiers[2]
                assert new_cn_id == "wb-gsm-sim1"
                assert changed is True
                assert isinstance(self.con_man.timeouts.deny_sim_switch_until, datetime.datetime)
                delta = self.con_man.timeouts.deny_sim_switch_until - TEST_NOW
                assert delta.total_seconds() == self.config.sticky_sim_period.total_seconds()

                active_cn = self.con_man.network_manager.get_active_connections().get("wb-gsm-sim1")
                new_tier, new_cn_id, changed = self.con_man.set_current_connection(
                    active_cn, "wb-gsm-sim1", self.config.tiers[2]
                )
                assert new_tier == self.config.tiers[2]
                assert new_cn_id == "wb-gsm-sim1"
                assert changed is False
                assert isinstance(self.con_man.timeouts.deny_sim_switch_until, datetime.datetime)
                delta = self.con_man.timeouts.deny_sim_switch_until - TEST_NOW
                assert delta.total_seconds() == self.config.sticky_sim_period.total_seconds()

                active_cn = self.con_man.network_manager.get_active_connections().get("wb-eth0")
                new_tier, new_cn_id, changed = self.con_man.set_current_connection(
                    active_cn, "wb-eth0", self.config.tiers[0]
                )
                assert new_tier == self.config.tiers[0]
                assert new_cn_id == "wb-eth0"
                assert changed is True
                assert self.con_man.timeouts.deny_sim_switch_until is None

                call_ifmetric_mock.assert_has_calls(
                    [
                        call('ppp0', 305), call('ppp0', 55), call('ppp0', 305)
                    ]
                )

    def test_14_find_lesser_gsm_connections(self):
        config_data = {
            "tiers": {
                "high": ["wb-gsm-sim1", "wb-eth1"],
                "normal": ["wb-wifi-client"],
                "low": ["wb-eth0", "wb-gsm-sim2"],
            }
        }
        self._init_con_man(config_data)
        self.net_man.add_ethernet(
            "wb-eth0", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        )
        self.net_man.add_ethernet(
            "wb-eth1", device_connected=True, connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN
        )
        self.net_man.add_gsm(
            "wb-gsm-sim1",
            device_connected=True,
            connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
            sim_slot=1,
        )
        self.net_man.add_gsm(
            "wb-gsm-sim2",
            device_connected=True,
            connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
            sim_slot=2,
        )

        lesser = list(self.con_man.find_lesser_gsm_connections("wb-gsm-sim1", self.config.tiers[0]))
        assert len(lesser) == 1
        assert lesser[0].get_connection_id() == "wb-gsm-sim2"

        lesser = list(self.con_man.find_lesser_gsm_connections("wb-eth1", self.config.tiers[0]))
        assert len(lesser) == 2
        assert lesser[0].get_connection_id() == "wb-gsm-sim1"
        assert lesser[1].get_connection_id() == "wb-gsm-sim2"

        lesser = list(self.con_man.find_lesser_gsm_connections("wb-gsm-sim2", self.config.tiers[2]))
        assert len(lesser) == 0

    def test_15_deactivate_lesser_gsm_connections_1(self):
        config_data = {"tiers": {"high": ["wb-gsm-sim1"], "normal": ["wb-eth0"], "low": ["wb-gsm-sim2"]}}

        self._init_con_man(config_data)
        self.net_man.add_gsm(
            "wb-gsm-sim1",
            device_connected=True,
            connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
            sim_slot=1,
        )
        self.net_man.add_gsm(
            "wb-gsm-sim2",
            device_connected=True,
            connection_state=NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
            sim_slot=2,
        )
        self.con_man.current_connection = "wb-eth0"
        self.con_man.current_tier = self.config.tiers[1]

        assert self._is_active_connection(self.con_man.get_active_connection("wb-gsm-sim1"))
        assert self._is_active_connection(self.con_man.get_active_connection("wb-gsm-sim2"))

        self.con_man.deactivate_lesser_gsm_connections(
            self.con_man.current_connection, self.con_man.current_tier
        )

        assert self._is_active_connection(self.con_man.get_active_connection("wb-gsm-sim1"))
        assert self.con_man.get_active_connection("wb-gsm-sim2") is None
