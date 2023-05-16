import datetime
import io
import json
import logging
import time
from datetime import timedelta
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

import dbus
import pycurl

from wb.nm_helper import connection_manager
from wb.nm_helper.network_manager import (
    NM_DEVICE_TYPE_ETHERNET,
    NM_DEVICE_TYPE_MODEM,
    NM_DEVICE_TYPE_WIFI,
)

# DUMMY CLASSES


class DummyNetworkManager:
    def get_connections(self):
        pass

    def find_connection(self):
        pass

    def find_device_for_connection(self):
        pass


class DummyNMDevice:
    def __init__(self):
        self.get_active_connection = None

    def get_property(self):
        pass


class DummyNMConnection:
    def __init__(self, name, settings):
        self.name = name
        self.settings = settings

    def get_sim_slot(self):
        pass

    def get_connection_id(self):
        return self.name

    def get_connection_type(self):
        return "typeof_" + self.name

    def get_settings(self):
        return self.settings


class DummyNMActiveConnection:
    def get_connection_id(self):
        pass

    def get_ifaces(self):
        pass


class DummyConfigFile:
    connectivity_check_url = "DUMMY_URL"
    connectivity_check_payload = "DUMMY_PAYLOAD"
    sticky_connection_period = datetime.timedelta(seconds=123)

    def load_config(self):
        pass


class DummyCurl:
    URL = 10001
    WRITEDATA = 10002
    INTERFACE = 10003

    def setopt(self):
        pass

    def perform(self):
        pass

    def close(self):
        pass


class DummyBytesIO:
    def getvalue(self):
        pass


class DummyModemManager:
    pass


# TESTS


class ConnectionTierTests(TestCase):
    def setUp(self) -> None:
        self.tier = connection_manager.ConnectionTier("DUMMY_NAME", 3, ["DUMMY_LIST_ITEM"])

    def test_init(self):
        self.assertEqual("DUMMY_NAME", self.tier.name)
        self.assertEqual(3, self.tier.priority)
        self.assertEqual(["DUMMY_LIST_ITEM"], self.tier.connections)

    def test_get_base_route_metric(self):
        self.assertEqual(105, self.tier.get_base_route_metric())

    def test_update_connections(self):
        self.tier.update_connections(["DUMMY_ANOTHER_ITEM"])

        self.assertEqual(["DUMMY_ANOTHER_ITEM"], self.tier.connections)


class ConfigFileTests(TestCase):
    def setUp(self) -> None:
        self.config = connection_manager.ConfigFile()

    def test_init(self):
        self.assertEqual(False, self.config.debug)
        self.assertEqual([], self.config.tiers)
        self.assertEqual(None, self.config.sticky_connection_period)
        self.assertEqual("", self.config.connectivity_check_url)
        self.assertEqual("", self.config.connectivity_check_payload)

    def test_load_config(self):
        # no tiers
        self.config.get_tiers = MagicMock(return_value=["DUMMY_TIERS"])
        self.config.get_connectivity_check_payload = MagicMock(return_value="DUMMY_PAYLOAD")
        self.config.get_connectivity_check_url = MagicMock(return_value="DUMMY_URL")
        self.config.get_sticky_connection_period = MagicMock(return_value="DUMMY_PERIOD")

        test_config = {"debug": "TEST_DEBUG"}
        self.config.load_config(test_config)

        self.assertEqual("TEST_DEBUG", self.config.debug)
        self.assertEqual("DUMMY_PAYLOAD", self.config.connectivity_check_payload)
        self.assertEqual("DUMMY_URL", self.config.connectivity_check_url)
        self.assertEqual("DUMMY_PERIOD", self.config.sticky_connection_period)
        self.assertEqual([], self.config.get_tiers.mock_calls)
        self.assertEqual([call(test_config)], self.config.get_sticky_connection_period.mock_calls)
        self.assertEqual([call(test_config)], self.config.get_connectivity_check_url.mock_calls)
        self.assertEqual([call(test_config)], self.config.get_sticky_connection_period.mock_calls)

        # with tiers
        self.config.get_tiers = MagicMock(return_value=["DUMMY_TIERS"])
        self.config.get_connectivity_check_payload = MagicMock(return_value="DUMMY_PAYLOAD")
        self.config.get_connectivity_check_url = MagicMock(return_value="DUMMY_URL")
        self.config.get_sticky_connection_period = MagicMock(return_value="DUMMY_PERIOD")

        test_config = {"debug": "TEST_DEBUG", "tiers": "DUMMY_TIERS_JSON"}
        self.config.load_config(test_config)

        self.assertEqual(["DUMMY_TIERS"], self.config.tiers)
        self.assertEqual("TEST_DEBUG", self.config.debug)
        self.assertEqual("DUMMY_PAYLOAD", self.config.connectivity_check_payload)
        self.assertEqual("DUMMY_URL", self.config.connectivity_check_url)
        self.assertEqual("DUMMY_PERIOD", self.config.sticky_connection_period)
        self.assertEqual([call(test_config)], self.config.get_tiers.mock_calls)
        self.assertEqual([call(test_config)], self.config.get_sticky_connection_period.mock_calls)
        self.assertEqual([call(test_config)], self.config.get_connectivity_check_url.mock_calls)
        self.assertEqual([call(test_config)], self.config.get_sticky_connection_period.mock_calls)

    def test_get_tiers(self):
        test_config = {
            "tiers": {
                "high": ["wb_eth0"],
                "medium": ["wb_eth0"],
                "low": ["wb_eth0"],
            }
        }

        with patch.object(connection_manager, "ConnectionTier") as dummy_tier:
            dummy_tier.side_effect = ["TIER1", "TIER2", "TIER3"]
            output = self.config.get_tiers(test_config)

        self.assertEqual(["TIER1", "TIER2", "TIER3"], output)

    def test_get_sticky_connection_period(self):
        # default
        output = self.config.get_sticky_connection_period({})

        self.assertEqual(connection_manager.DEFAULT_STICKY_CONNECTION_PERIOD, output)

        # invalid
        with self.assertRaises(connection_manager.ImproperlyConfigured):
            self.config.get_sticky_connection_period({"sticky_connection_period_s": "ABC"})

        # valid
        output = self.config.get_sticky_connection_period({"sticky_connection_period_s": 13})

        self.assertEqual(timedelta(seconds=13), output)

    def test_get_connectivity_check_url(self):
        # http
        self.assertEqual(
            "http://example",
            self.config.get_connectivity_check_url({"connectivity_check_url": "http://example"}),
        )

        # https
        self.assertEqual(
            "https://example",
            self.config.get_connectivity_check_url({"connectivity_check_url": "https://example"}),
        )

        # error
        with self.assertRaises(connection_manager.ImproperlyConfigured):
            self.config.get_connectivity_check_url({"connectivity_check_url": "example"})

    def test_get_connectivity_check_payload(self):
        # default
        self.assertEqual(
            connection_manager.DEFAULT_CONNECTIVITY_CHECK_PAYLOAD,
            self.config.get_connectivity_check_payload({}),
        )

        # invalid
        with self.assertRaises(connection_manager.ImproperlyConfigured):
            self.config.get_connectivity_check_payload({"connectivity_check_payload": ""})

        # valid
        self.assertEqual(
            "ABC", self.config.get_connectivity_check_payload({"connectivity_check_payload": "ABC"})
        )

    def test_has_connections(self):
        # false
        self.config.tiers = [connection_manager.ConnectionTier(name="dummy", priority=1, connections=[])]
        self.assertFalse(self.config.has_connections())

        # true
        self.config.tiers = [
            connection_manager.ConnectionTier(name="dummy", priority=1, connections=["wb_eth0"])
        ]
        self.assertTrue(self.config.has_connections())


class NetworkAwareConfigFileTests(TestCase):
    def setUp(self) -> None:
        self.config = connection_manager.NetworkAwareConfigFile(network_manager=DummyNetworkManager())

    def test_init(self):
        with patch.object(connection_manager.ConfigFile, "__init__") as mock_init:
            config = connection_manager.NetworkAwareConfigFile(network_manager="DUMMY_NM")
            self.assertEqual("DUMMY_NM", config.network_manager)
            self.assertEqual([call()], mock_init.mock_calls)

    def test_load_config(self):
        test_config = {"DUMMY": "CONFIG"}

        # no tiers
        self.config.get_default_tiers = MagicMock(return_value=["DUMMY_DEFAULT_TIERS"])
        self.config.filter_out_unmanaged_connections = MagicMock()
        self.config.tiers = []

        with patch.object(connection_manager.ConfigFile, "load_config") as mock_load_config:
            self.config.load_config(test_config)

        self.assertEqual([call(test_config)], mock_load_config.mock_calls)
        self.assertEqual([call()], self.config.get_default_tiers.mock_calls)
        self.assertEqual([call()], self.config.filter_out_unmanaged_connections.mock_calls)

        # with tiers
        self.config.get_default_tiers = MagicMock(return_value=["DUMMY_DEFAULT_TIERS"])
        self.config.filter_out_unmanaged_connections = MagicMock()
        self.config.tiers = ["DUMMY_TIER"]

        with patch.object(connection_manager.ConfigFile, "load_config") as mock_load_config:
            self.config.load_config(test_config)

        self.assertEqual([call(test_config)], mock_load_config.mock_calls)
        self.assertEqual([], self.config.get_default_tiers.mock_calls)
        self.assertEqual([call()], self.config.filter_out_unmanaged_connections.mock_calls)

    def test_filter_out_unmanaged_connections(self):
        test_tier = connection_manager.ConnectionTier(
            name="dummy", priority=1, connections=["wb_eth0", "wb_eth1", "wb_eth2"]
        )
        self.config.tiers = [test_tier]

        # all valid
        self.config.is_connection_unmanaged = MagicMock(side_effect=[False, False, False])
        self.config.network_manager.find_connection = MagicMock(side_effect=["DEV1", "DEV2", "DEV3"])
        test_tier.update_connections = MagicMock()

        self.config.filter_out_unmanaged_connections()

        self.assertEqual(
            [call("wb_eth0"), call("wb_eth1"), call("wb_eth2")],
            self.config.network_manager.find_connection.mock_calls,
        )
        self.assertEqual(
            [call("DEV1"), call("DEV2"), call("DEV3")], self.config.is_connection_unmanaged.mock_calls
        )
        self.assertEqual([], test_tier.update_connections.mock_calls)

        # has unmanaged connection
        self.config.is_connection_unmanaged = MagicMock(side_effect=[False, True, False])
        self.config.network_manager.find_connection = MagicMock(side_effect=["DEV1", "DEV2", "DEV3"])
        test_tier.update_connections = MagicMock()

        self.config.filter_out_unmanaged_connections()

        self.assertEqual(
            [call("wb_eth0"), call("wb_eth1"), call("wb_eth2")],
            self.config.network_manager.find_connection.mock_calls,
        )
        self.assertEqual(
            [call("DEV1"), call("DEV2"), call("DEV3")], self.config.is_connection_unmanaged.mock_calls
        )
        self.assertEqual([call(["wb_eth0", "wb_eth2"])], test_tier.update_connections.mock_calls)

        # has un-findable connection
        self.config.is_connection_unmanaged = MagicMock(side_effect=[False, False])
        self.config.network_manager.find_connection = MagicMock(side_effect=["DEV1", "DEV2", None])
        test_tier.update_connections = MagicMock()

        self.config.filter_out_unmanaged_connections()

        self.assertEqual(
            [call("wb_eth0"), call("wb_eth1"), call("wb_eth2")],
            self.config.network_manager.find_connection.mock_calls,
        )
        self.assertEqual([call("DEV1"), call("DEV2")], self.config.is_connection_unmanaged.mock_calls)
        self.assertEqual([call(["wb_eth0", "wb_eth1"])], test_tier.update_connections.mock_calls)

    def test_get_default_tiers(self):
        con_eth = DummyNMConnection(
            "wb_eth0", {"connection": {"autoconnect": True}, "ipv4": {"never-default": False}}
        )
        con_not_ac = DummyNMConnection(
            "wb_eth1", {"connection": {"autoconnect": False}, "ipv4": {"never-default": False}}
        )
        con_nd = DummyNMConnection(
            "wb_eth2", {"connection": {"autoconnect": True}, "ipv4": {"never-default": True}}
        )
        con_unm = DummyNMConnection(
            "wb_eth3", {"connection": {"autoconnect": True}, "ipv4": {"never-default": False}}
        )
        con_wifi = DummyNMConnection(
            "wb_wifi_client", {"connection": {"autoconnect": True}, "ipv4": {"never-default": False}}
        )
        con_gsm = DummyNMConnection(
            "wb_gsm_sim1", {"connection": {"autoconnect": True}, "ipv4": {"never-default": False}}
        )
        con_unk = DummyNMConnection(
            "wb_unk", {"connection": {"autoconnect": True}, "ipv4": {"never-default": False}}
        )
        test_connections = [con_eth, con_not_ac, con_nd, con_unm, con_wifi, con_gsm, con_unk]

        self.config.is_connection_unmanaged = MagicMock(
            side_effect=(False, False, False, True, False, False, False)
        )
        self.config.network_manager.get_connections = MagicMock(return_value=test_connections)

        with patch.object(connection_manager, "connection_type_to_device_type") as mock_ct_to_dt:
            mock_ct_to_dt.side_effect = (
                NM_DEVICE_TYPE_ETHERNET,
                NM_DEVICE_TYPE_ETHERNET,
                NM_DEVICE_TYPE_ETHERNET,
                NM_DEVICE_TYPE_ETHERNET,
                NM_DEVICE_TYPE_WIFI,
                NM_DEVICE_TYPE_MODEM,
                31337,
            )
            output = self.config.get_default_tiers()

        self.assertEqual([call()], self.config.network_manager.get_connections.mock_calls)
        self.assertEqual(
            [
                call(con_eth),
                call(con_not_ac),
                call(con_nd),
                call(con_unm),
                call(con_wifi),
                call(con_gsm),
                call(con_unk),
            ],
            self.config.is_connection_unmanaged.mock_calls,
        )
        self.assertEqual(3, len(output))
        self.assertEqual("high", output[0].name)
        self.assertEqual(3, output[0].priority)
        self.assertEqual(["wb_eth0"], output[0].connections)
        self.assertEqual("medium", output[1].name)
        self.assertEqual(2, output[1].priority)
        self.assertEqual(["wb_wifi_client"], output[1].connections)
        self.assertEqual("low", output[2].name)
        self.assertEqual(1, output[2].priority)
        self.assertEqual(["wb_gsm_sim1"], output[2].connections)
        self.assertEqual(
            [
                call("typeof_wb_eth0"),
                call("typeof_wb_eth1"),
                call("typeof_wb_eth2"),
                call("typeof_wb_eth3"),
                call("typeof_wb_wifi_client"),
                call("typeof_wb_gsm_sim1"),
                call("typeof_wb_unk"),
            ],
            mock_ct_to_dt.mock_calls,
        )

    def test_is_connection_unmanaged(self):
        with self.assertRaises(AssertionError):
            self.config.is_connection_unmanaged(None)

        test_con = DummyNMConnection("dummy", {})
        test_dev = DummyNMDevice()
        self.config.network_manager.find_device_for_connection = MagicMock(
            side_effect=[None, test_dev, test_dev, test_dev]
        )
        test_dev.get_property = MagicMock(side_effect=[True, "dev2", 1, "dev3", "dummy", "dev4"])

        value1 = self.config.is_connection_unmanaged(test_con)  # no device will be returned
        value2 = self.config.is_connection_unmanaged(test_con)  # True will be returned for managed
        value3 = self.config.is_connection_unmanaged(test_con)  # 1 will be returned for managed
        value4 = self.config.is_connection_unmanaged(test_con)  # random value will be returned for managed

        self.assertEqual(
            [call(test_con), call(test_con), call(test_con), call(test_con)],
            self.config.network_manager.find_device_for_connection.mock_calls,
        )
        self.assertEqual(
            [
                call("Managed"),
                call("Interface"),
                call("Managed"),
                call("Interface"),
                call("Managed"),
                call("Interface"),
            ],
            test_dev.get_property.mock_calls,
        )
        self.assertEqual([True, False, False, True], [value1, value2, value3, value4])


class TimeoutManagerTests(TestCase):
    def setUp(self) -> None:
        self.fake_now = datetime.datetime(year=2000, month=1, day=1, hour=23, minute=13, second=4)
        self.tm = connection_manager.TimeoutManager(connection_manager.ConfigFile())
        self.tm.now = MagicMock(return_value=self.fake_now)

    def test_init(self):
        self.tm = connection_manager.TimeoutManager("DUMMY_CONFIG")

        self.assertEqual("DUMMY_CONFIG", self.tm.config)
        self.assertEqual({}, self.tm.connection_retry_timeouts)
        self.assertEqual(None, self.tm.keep_sticky_connections_until)
        self.assertEqual(
            connection_manager.CONNECTION_ACTIVATION_TIMEOUT, self.tm.connection_activation_timeout
        )

    def test_now(self):
        self.tm = connection_manager.TimeoutManager(connection_manager.ConfigFile())
        self.assertTrue(isinstance(self.tm.now(), datetime.datetime))

    def test_debug_log_timeouts(self):
        self.tm.keep_sticky_connections_until = "DUMMY1"
        self.tm.connection_retry_timeouts = {"DUMMY2": 31337, "DUMMY3": 31338}

        with patch.object(logging, "debug") as mock_debug:
            self.tm.debug_log_timeouts()

        self.assertEqual(
            [
                call("Sticky Connections Timeout: %s", "DUMMY1"),
                call("Connection Retry Timeout for %s: %s", "DUMMY2", 31337),
                call("Connection Retry Timeout for %s: %s", "DUMMY3", 31338),
            ],
            mock_debug.mock_calls,
        )

    def test_touch_connection_retry_timeout(self):
        self.tm.touch_connection_retry_timeout("dummy_con")

        self.assertEqual([call()], self.tm.now.mock_calls)
        self.assertEqual(
            {"dummy_con": self.fake_now + connection_manager.CONNECTION_ACTIVATION_RETRY_TIMEOUT},
            self.tm.connection_retry_timeouts,
        )

    def test_reset_connection_retry_timeout(self):
        self.tm.reset_connection_retry_timeout("dummy_con")

        self.assertEqual([call()], self.tm.now.mock_calls)
        self.assertEqual({"dummy_con": self.fake_now}, self.tm.connection_retry_timeouts)

    def test_touch_sticky_timeout(self):
        test_con = DummyNMConnection("dummy", {})
        test_con.get_connection_type = MagicMock(side_effect=["DEV1", "DEV2", "DEV3"])
        self.tm.keep_sticky_connections_until = 31337
        self.tm.config.sticky_connection_period = datetime.timedelta(seconds=1)

        with patch.object(connection_manager, "connection_type_to_device_type") as mock_ct_to_dt:
            mock_ct_to_dt.side_effect = [NM_DEVICE_TYPE_ETHERNET, NM_DEVICE_TYPE_WIFI, NM_DEVICE_TYPE_MODEM]
            self.tm.touch_sticky_timeout(test_con)  # ethernet
            self.assertIsNone(self.tm.keep_sticky_connections_until)
            self.tm.touch_sticky_timeout(test_con)  # wifi
            self.assertEqual(
                self.fake_now + self.tm.config.sticky_connection_period, self.tm.keep_sticky_connections_until
            )
            self.tm.touch_sticky_timeout(test_con)  # modem
            self.assertEqual(
                self.fake_now + self.tm.config.sticky_connection_period, self.tm.keep_sticky_connections_until
            )

    def test_connection_retry_timeout_is_active(self):
        self.tm.connection_retry_timeouts = {}
        self.assertFalse(self.tm.connection_retry_timeout_is_active("dummy_con"))

        self.tm.connection_retry_timeouts = {"dummy_con": self.fake_now - datetime.timedelta(seconds=1)}
        self.assertFalse(self.tm.connection_retry_timeout_is_active("dummy_con"))

        self.tm.connection_retry_timeouts = {"dummy_con": self.fake_now}
        self.assertTrue(self.tm.connection_retry_timeout_is_active("dummy_con"))

        self.tm.connection_retry_timeouts = {"dummy_con": self.fake_now + datetime.timedelta(seconds=1)}
        self.assertTrue(self.tm.connection_retry_timeout_is_active("dummy_con"))

    def test_sticky_timeout_is_active(self):
        self.tm.keep_sticky_connections_until = None
        self.assertFalse(self.tm.sticky_timeout_is_active())

        self.tm.keep_sticky_connections_until = self.fake_now - datetime.timedelta(seconds=1)
        self.assertFalse(self.tm.sticky_timeout_is_active())

        self.tm.keep_sticky_connections_until = self.fake_now
        self.assertFalse(self.tm.sticky_timeout_is_active())

        self.tm.keep_sticky_connections_until = self.fake_now + datetime.timedelta(seconds=1)
        self.assertTrue(self.tm.sticky_timeout_is_active())


class SingleFunctionTests(TestCase):
    def test_read_config_json(self):
        with patch.object(connection_manager, "CONFIG_FILE", "TEST-NON-EXISTENT-FILE"):
            with self.assertRaises(FileNotFoundError):
                connection_manager.read_config_json()

        with patch.object(connection_manager, "CONFIG_FILE", __file__):
            with patch.object(json, "loads") as mock_load:
                mock_load.return_value = "DUMMY_OUTPUT"
                self.assertEqual("DUMMY_OUTPUT", connection_manager.read_config_json())
                self.assertEqual(1, mock_load.call_count)

    def test_curl_get(self):
        DummyCurl.setopt = MagicMock()
        DummyBytesIO.getvalue = MagicMock(return_value="ЖЖЖ".encode("UTF8"))
        with patch.object(pycurl, "Curl", DummyCurl):
            with patch.object(io, "BytesIO", DummyBytesIO):
                output = connection_manager.curl_get("dummy_if", "dummy_url")
                self.assertEqual(5, DummyCurl.setopt.call_count)
                self.assertEqual(call(10001, "dummy_url"), DummyCurl.setopt.mock_calls[0])
                self.assertEqual(2, len(DummyCurl.setopt.mock_calls[1].args))
                self.assertEqual(10002, DummyCurl.setopt.mock_calls[1].args[0])
                self.assertTrue(isinstance(DummyCurl.setopt.mock_calls[1].args[1], DummyBytesIO))
                self.assertEqual(call(10003, "dummy_if"), DummyCurl.setopt.mock_calls[2])
                self.assertEqual(
                    call(pycurl.CONNECTTIMEOUT, connection_manager.CONNECTIVITY_CHECK_TIMEOUT),
                    DummyCurl.setopt.mock_calls[3],
                )
                self.assertEqual(
                    call(pycurl.TIMEOUT, connection_manager.CONNECTIVITY_CHECK_TIMEOUT),
                    DummyCurl.setopt.mock_calls[4],
                )

    def test_check_connectivity(self):
        # with auto config
        DummyConfigFile.load_config = MagicMock()
        dummy_active_cn = DummyNMActiveConnection()
        dummy_active_cn.get_ifaces = MagicMock(
            side_effect=[[], ["dummy_iface1"], ["dummy_iface2"], ["dummy_iface3"]]
        )

        with patch.object(connection_manager, "curl_get") as mock_curl_get, patch.object(
            connection_manager, "read_config_json"
        ) as mock_read_config_json, patch.object(connection_manager, "ConfigFile", DummyConfigFile):
            mock_curl_get.side_effect = ["DUMMY_INVALID_PAYLOAD", "DUMMY_PAYLOAD", "DUMMY_PAYLOAD"]

            mock_read_config_json.return_value = {"dummy": "config"}

            result = connection_manager.check_connectivity(dummy_active_cn)  # no ifaces
            self.assertEqual(False, result)

            result = connection_manager.check_connectivity(dummy_active_cn)  # payload mismatch
            self.assertEqual(False, result)

            result = connection_manager.check_connectivity(dummy_active_cn)  # payload match
            self.assertEqual(True, result)

            mock_curl_get.side_effect = pycurl.error()
            result = connection_manager.check_connectivity(dummy_active_cn)  # exception
            self.assertEqual(False, result)

        self.assertEqual(
            [
                call({"dummy": "config"}),
                call({"dummy": "config"}),
                call({"dummy": "config"}),
                call({"dummy": "config"}),
            ],
            DummyConfigFile.load_config.mock_calls,
        )
        self.assertEqual([call(), call(), call(), call()], dummy_active_cn.get_ifaces.mock_calls)
        self.assertEqual(
            [
                call("dummy_iface1", "DUMMY_URL"),
                call("dummy_iface2", "DUMMY_URL"),
                call("dummy_iface3", "DUMMY_URL"),
            ],
            mock_curl_get.mock_calls,
        )

        # with external config provided
        DummyConfigFile.load_config = MagicMock()
        dummy_config = DummyConfigFile()
        dummy_config.connectivity_check_payload = "NEW_DUMMY_PAYLOAD"
        dummy_config.connectivity_check_url = "NEW_DUMMY_URL"
        dummy_active_cn.get_ifaces = MagicMock(side_effect=[["dummy_iface4"]])

        with patch.object(connection_manager, "curl_get") as mock_curl_get, patch.object(
            connection_manager, "read_config_json"
        ) as mock_read_config_json:
            mock_curl_get.return_value = "NEW_DUMMY_PAYLOAD"
            mock_read_config_json.return_value = {"dummy": "config"}
            result = connection_manager.check_connectivity(dummy_active_cn, config=dummy_config)

        self.assertEqual([call()], dummy_active_cn.get_ifaces.mock_calls)
        self.assertEqual([], DummyConfigFile.load_config.mock_calls)

        self.assertEqual([call("dummy_iface4", "NEW_DUMMY_URL")], mock_curl_get.mock_calls)
        self.assertEqual(True, result)


class ConnectionManagerTests(TestCase):
    def setUp(self) -> None:
        self.config = DummyConfigFile()
        self.cm = connection_manager.ConnectionManager(
            config=self.config, network_manager=DummyNetworkManager(), modem_manager=DummyModemManager()
        )

    def test_check_current_connection(self):
        high_tier = connection_manager.ConnectionTier(name="high", priority=1, connections=["wb_eth0"])
        self.cm.config.tiers = [high_tier]
        self.cm.current_tier = high_tier
        self.cm.current_connection = "wb_eth0"

        # connectivity ok
        with patch.object(connection_manager, "check_connectivity", MagicMock(return_value=True)):
            self.cm._log_connection_check_error = MagicMock()
            self.cm.find_activated_connection = MagicMock(side_effect=["dummy_con1"])
            self.assertTrue(self.cm.check_current_connection())
            self.assertEqual([], self.cm._log_connection_check_error.mock_calls)
            self.assertEqual(
                [call("dummy_con1", self.config)], connection_manager.check_connectivity.mock_calls
            )
            self.assertEqual([call("wb_eth0")], self.cm.find_activated_connection.mock_calls)

        # no connectivity
        with patch.object(connection_manager, "check_connectivity", MagicMock(return_value=False)):
            self.cm._log_connection_check_error = MagicMock()
            self.cm.find_activated_connection = MagicMock(side_effect=["dummy_con2"])
            self.assertFalse(self.cm.check_current_connection())
            self.assertEqual([], self.cm._log_connection_check_error.mock_calls)
            self.assertEqual(
                [call("dummy_con2", self.config)], connection_manager.check_connectivity.mock_calls
            )
            self.assertEqual([call("wb_eth0")], self.cm.find_activated_connection.mock_calls)

        # exception
        with patch.object(connection_manager, "check_connectivity", MagicMock()):
            self.cm._log_connection_check_error = MagicMock()
            self.cm.find_activated_connection = MagicMock(side_effect=dbus.exceptions.DBusException())
            self.assertFalse(self.cm.check_current_connection())
            self.assertEqual(
                [call("wb_eth0", self.cm.find_activated_connection.side_effect)],
                self.cm._log_connection_check_error.mock_calls,
            )
            self.assertEqual([], connection_manager.check_connectivity.mock_calls)
            self.assertEqual([call("wb_eth0")], self.cm.find_activated_connection.mock_calls)

    def test_check_non_current_connection(self):
        high_tier = connection_manager.ConnectionTier(name="high", priority=1, connections=["wb_eth0"])
        self.cm.config.tiers = [high_tier]
        self.cm.current_tier = high_tier
        self.cm.current_connection = "wb_eth0"

        # skip current
        with patch.object(connection_manager, "check_connectivity", MagicMock()):
            self.cm._log_connection_check_error = MagicMock()
            self.cm.ok_to_activate_connection = MagicMock()
            self.cm.find_activated_connection = MagicMock()
            self.cm.activate_connection = MagicMock()
            self.cm.timeouts.touch_connection_retry_timeout = MagicMock()
            self.assertFalse(self.cm.check_non_current_connection(high_tier, "wb_eth0"))
            self.assertEqual([], self.cm._log_connection_check_error.mock_calls)
            self.assertEqual([], connection_manager.check_connectivity.mock_calls)
            self.assertEqual([], self.cm.find_activated_connection.mock_calls)
            self.assertEqual([], self.cm.activate_connection.mock_calls)
            self.assertEqual([], self.cm.ok_to_activate_connection.mock_calls)
            self.assertEqual([], self.cm.timeouts.touch_connection_retry_timeout.mock_calls)

        # exception
        with patch.object(connection_manager, "check_connectivity", MagicMock()):
            self.cm._log_connection_check_error = MagicMock()
            self.cm.ok_to_activate_connection = MagicMock()
            self.cm.find_activated_connection = MagicMock(side_effect=dbus.exceptions.DBusException())
            self.cm.activate_connection = MagicMock()
            self.cm.timeouts.touch_connection_retry_timeout = MagicMock()
            self.assertFalse(self.cm.check_non_current_connection(high_tier, "wb_eth1"))
            self.assertEqual(
                [call("wb_eth1", self.cm.find_activated_connection.side_effect)],
                self.cm._log_connection_check_error.mock_calls,
            )
            self.assertEqual([], connection_manager.check_connectivity.mock_calls)
            self.assertEqual([call("wb_eth1")], self.cm.find_activated_connection.mock_calls)
            self.assertEqual([], self.cm.activate_connection.mock_calls)
            self.assertEqual([], self.cm.ok_to_activate_connection.mock_calls)
            self.assertEqual([call("wb_eth1")], self.cm.timeouts.touch_connection_retry_timeout.mock_calls)

        # active and has connectivity
        with patch.object(connection_manager, "check_connectivity", MagicMock(return_value=True)):
            self.cm._log_connection_check_error = MagicMock()
            self.cm.ok_to_activate_connection = MagicMock()
            self.cm.find_activated_connection = MagicMock(return_value="dev1")
            self.cm.activate_connection = MagicMock()
            self.cm.timeouts.touch_connection_retry_timeout = MagicMock()
            self.assertTrue(self.cm.check_non_current_connection(high_tier, "wb_eth1"))
            self.assertEqual([call("wb_eth1")], self.cm.find_activated_connection.mock_calls)
            self.assertEqual([call("dev1", self.config)], connection_manager.check_connectivity.mock_calls)
            self.assertEqual([], self.cm.activate_connection.mock_calls)
            self.assertEqual([], self.cm.ok_to_activate_connection.mock_calls)
            self.assertEqual([], self.cm.timeouts.touch_connection_retry_timeout.mock_calls)
            self.assertEqual([], self.cm._log_connection_check_error.mock_calls)

        # not active but activated and has connectivity
        with patch.object(connection_manager, "check_connectivity", MagicMock(return_value=True)):
            self.cm._log_connection_check_error = MagicMock()
            self.cm.ok_to_activate_connection = MagicMock(return_value=True)
            self.cm.find_activated_connection = MagicMock(return_value=None)
            self.cm.activate_connection = MagicMock(return_value="dev1")
            self.cm.timeouts.touch_connection_retry_timeout = MagicMock()
            self.assertTrue(self.cm.check_non_current_connection(high_tier, "wb_eth1"))
            self.assertEqual([call("wb_eth1")], self.cm.find_activated_connection.mock_calls)
            self.assertEqual([call("wb_eth1")], self.cm.ok_to_activate_connection.mock_calls)
            self.assertEqual([call("wb_eth1")], self.cm.activate_connection.mock_calls)
            self.assertEqual([call("dev1", self.config)], connection_manager.check_connectivity.mock_calls)
            self.assertEqual([call("wb_eth1")], self.cm.timeouts.touch_connection_retry_timeout.mock_calls)
            self.assertEqual([], self.cm._log_connection_check_error.mock_calls)

        # not active, not ok to activate
        with patch.object(connection_manager, "check_connectivity", MagicMock()):
            self.cm._log_connection_check_error = MagicMock()
            self.cm.ok_to_activate_connection = MagicMock(return_value=False)
            self.cm.find_activated_connection = MagicMock(return_value=None)
            self.cm.activate_connection = MagicMock(return_value="dev1")
            self.cm.timeouts.touch_connection_retry_timeout = MagicMock()
            self.assertFalse(self.cm.check_non_current_connection(high_tier, "wb_eth1"))
            self.assertEqual([call("wb_eth1")], self.cm.find_activated_connection.mock_calls)
            self.assertEqual([call("wb_eth1")], self.cm.ok_to_activate_connection.mock_calls)
            self.assertEqual([], self.cm.activate_connection.mock_calls)
            self.assertEqual([], connection_manager.check_connectivity.mock_calls)
            self.assertEqual([], self.cm.timeouts.touch_connection_retry_timeout.mock_calls)
            self.assertEqual([], self.cm._log_connection_check_error.mock_calls)

        # not active, failed to activate
        with patch.object(connection_manager, "check_connectivity", MagicMock()):
            self.cm._log_connection_check_error = MagicMock()
            self.cm.ok_to_activate_connection = MagicMock(return_value=True)
            self.cm.find_activated_connection = MagicMock(return_value=None)
            self.cm.activate_connection = MagicMock(return_value=None)
            self.cm.timeouts.touch_connection_retry_timeout = MagicMock()
            self.assertFalse(self.cm.check_non_current_connection(high_tier, "wb_eth1"))
            self.assertEqual([call("wb_eth1")], self.cm.find_activated_connection.mock_calls)
            self.assertEqual([call("wb_eth1")], self.cm.ok_to_activate_connection.mock_calls)
            self.assertEqual([call("wb_eth1")], self.cm.activate_connection.mock_calls)
            self.assertEqual([], connection_manager.check_connectivity.mock_calls)
            self.assertEqual([call("wb_eth1")], self.cm.timeouts.touch_connection_retry_timeout.mock_calls)
            self.assertEqual([], self.cm._log_connection_check_error.mock_calls)

    def test_check(self):
        high_tier = connection_manager.ConnectionTier(name="high", priority=1, connections=["wb_eth0"])
        low_tier = connection_manager.ConnectionTier(name="low", priority=3, connections=["wb_wifi_client"])
        self.cm.config.tiers = [high_tier, low_tier]
        self.cm.current_tier = high_tier
        self.cm.current_connection = "wb_eth0"

        # current is ok
        self.cm.timeouts.debug_log_timeouts = MagicMock()
        self.cm.check_current_connection = MagicMock(return_value=True)
        self.cm.check_non_current_connection = MagicMock()
        self.assertEqual((high_tier, "wb_eth0"), self.cm.check())
        self.assertEqual([call()], self.cm.check_current_connection.mock_calls)
        self.assertEqual([], self.cm.check_non_current_connection.mock_calls)

        # non-current is ok
        self.cm.timeouts.debug_log_timeouts = MagicMock()
        self.cm.check_current_connection = MagicMock(return_value=False)
        self.cm.check_non_current_connection = MagicMock(side_effect=[False, True])
        self.assertEqual((low_tier, "wb_wifi_client"), self.cm.check())
        self.assertEqual([call()], self.cm.check_current_connection.mock_calls)
        self.assertEqual(
            [call(high_tier, "wb_eth0"), call(low_tier, "wb_wifi_client")],
            self.cm.check_non_current_connection.mock_calls,
        )

        # everything is down
        self.cm.timeouts.debug_log_timeouts = MagicMock()
        self.cm.check_current_connection = MagicMock(return_value=False)
        self.cm.check_non_current_connection = MagicMock(side_effect=[False, False])
        self.assertEqual((high_tier, "wb_eth0"), self.cm.check())
        self.assertEqual([call()], self.cm.check_current_connection.mock_calls)
        self.assertEqual(
            [call(high_tier, "wb_eth0"), call(low_tier, "wb_wifi_client")],
            self.cm.check_non_current_connection.mock_calls,
        )

    def test__log_connection_check_error(self):
        with patch.object(logging, "warning") as mock_warning:
            ex = Exception("Test")
            self.cm._log_connection_check_error("wb_eth3", ex)
        self.assertEqual(1, mock_warning.call_count)

    def test_activate_connection(self):
        # connection not found
        dummy_con = DummyNMConnection("wb_eth6", {})
        dummy_con.get_connection_type = MagicMock()
        self.cm.find_connection = MagicMock(return_value=None)
        self.cm._find_device_for_connection = MagicMock()
        self.cm._activate_connection_with_type = MagicMock()
        with patch.object(connection_manager, "connection_type_to_device_type") as dummy_ct_to_dt:
            result = self.cm.activate_connection("wb_eth6")
        self.assertEqual(None, result)
        self.assertEqual([call("wb_eth6")], self.cm.find_connection.mock_calls)
        self.assertEqual([], self.cm._find_device_for_connection.mock_calls)
        self.assertEqual([], dummy_ct_to_dt.mock_calls)
        self.assertEqual([], self.cm._activate_connection_with_type.mock_calls)

        # device not found
        dummy_con = DummyNMConnection("wb_eth6", {})
        dummy_con.get_connection_type = MagicMock()
        self.cm.find_connection = MagicMock(return_value=dummy_con)
        self.cm._find_device_for_connection = MagicMock(return_value=None)
        self.cm._activate_connection_with_type = MagicMock()
        with patch.object(connection_manager, "connection_type_to_device_type") as dummy_ct_to_dt:
            result = self.cm.activate_connection("wb_eth6")
        self.assertEqual(None, result)
        self.assertEqual([call("wb_eth6")], self.cm.find_connection.mock_calls)
        self.assertEqual([call(dummy_con, "wb_eth6")], self.cm._find_device_for_connection.mock_calls)
        self.assertEqual([], dummy_ct_to_dt.mock_calls)
        self.assertEqual([], self.cm._activate_connection_with_type.mock_calls)

        # success
        dummy_con = DummyNMConnection("wb_eth6", {})
        dummy_con.get_connection_type = MagicMock(return_value="DUMMY_CON_TYPE")
        self.cm.find_connection = MagicMock(return_value=dummy_con)
        self.cm._find_device_for_connection = MagicMock(return_value="DUMMY_DEV")
        self.cm._activate_connection_with_type = MagicMock(return_value="ACTIVATION_RESULT")
        with patch.object(connection_manager, "connection_type_to_device_type") as dummy_ct_to_dt:
            dummy_ct_to_dt.return_value = "DUMMY_DEV_TYPE"
            result = self.cm.activate_connection("wb_eth6")
        self.assertEqual("ACTIVATION_RESULT", result)
        self.assertEqual([call("wb_eth6")], self.cm.find_connection.mock_calls)
        self.assertEqual([call(dummy_con, "wb_eth6")], self.cm._find_device_for_connection.mock_calls)
        self.assertEqual([call("DUMMY_CON_TYPE")], dummy_ct_to_dt.mock_calls)
        self.assertEqual(
            [call("DUMMY_DEV", dummy_con, "DUMMY_DEV_TYPE", "DUMMY_CON_TYPE", "wb_eth6")],
            self.cm._activate_connection_with_type.mock_calls,
        )

    def test__activate_connection_with_type(self):
        # ethernet
        self.cm._activate_generic_connection = MagicMock(return_value="ETH_RESULT")
        self.cm._activate_wifi_connection = MagicMock()
        self.cm._activate_gsm_connection = MagicMock()
        result = self.cm._activate_connection_with_type(
            "DUMMY_DEV", "DUMMY_CON", NM_DEVICE_TYPE_ETHERNET, "CON_TYPE", "CON_ID"
        )
        self.assertEqual("ETH_RESULT", result)
        self.assertEqual(
            [call.__bool__(), call("DUMMY_DEV", "DUMMY_CON")], self.cm._activate_generic_connection.mock_calls
        )
        self.assertEqual([], self.cm._activate_wifi_connection.mock_calls)
        self.assertEqual([], self.cm._activate_gsm_connection.mock_calls)

        # wifi
        self.cm._activate_generic_connection = MagicMock()
        self.cm._activate_wifi_connection = MagicMock(return_value="WIFI_RESULT")
        self.cm._activate_gsm_connection = MagicMock()
        result = self.cm._activate_connection_with_type(
            "DUMMY_DEV", "DUMMY_CON", NM_DEVICE_TYPE_WIFI, "CON_TYPE", "CON_ID"
        )
        self.assertEqual("WIFI_RESULT", result)
        self.assertEqual([], self.cm._activate_generic_connection.mock_calls)
        self.assertEqual(
            [call.__bool__(), call("DUMMY_DEV", "DUMMY_CON")], self.cm._activate_wifi_connection.mock_calls
        )
        self.assertEqual([], self.cm._activate_gsm_connection.mock_calls)

        # modem
        self.cm._activate_generic_connection = MagicMock()
        self.cm._activate_wifi_connection = MagicMock()
        self.cm._activate_gsm_connection = MagicMock(return_value="MODEM_RESULT")
        result = self.cm._activate_connection_with_type(
            "DUMMY_DEV", "DUMMY_CON", NM_DEVICE_TYPE_MODEM, "CON_TYPE", "CON_ID"
        )
        self.assertEqual("MODEM_RESULT", result)
        self.assertEqual([], self.cm._activate_generic_connection.mock_calls)
        self.assertEqual([], self.cm._activate_wifi_connection.mock_calls)
        self.assertEqual(
            [call.__bool__(), call("DUMMY_DEV", "DUMMY_CON")], self.cm._activate_gsm_connection.mock_calls
        )

        # unknown type
        self.cm._activate_generic_connection = MagicMock()
        self.cm._activate_wifi_connection = MagicMock()
        self.cm._activate_gsm_connection = MagicMock()
        with patch.object(logging, "warning") as mock_warning:
            result = self.cm._activate_connection_with_type(
                "DUMMY_DEV", "DUMMY_CON", 31337, "CON_TYPE", "CON_ID"
            )
        self.assertEqual(None, result)
        self.assertEqual([], self.cm._activate_generic_connection.mock_calls)
        self.assertEqual([], self.cm._activate_wifi_connection.mock_calls)
        self.assertEqual([], self.cm._activate_gsm_connection.mock_calls)
        self.assertEqual(1, mock_warning.call_count)

    def test_find_connection(self):
        # not found
        self.cm.network_manager.find_connection = MagicMock(return_value=None)
        with patch.object(logging, "warning") as mock_warning:
            result = self.cm.find_connection("DUMMY_CON")
        self.assertEqual(None, result)
        self.assertEqual([call("DUMMY_CON")], self.cm.network_manager.find_connection.mock_calls)
        self.assertEqual(1, mock_warning.call_count)

        # found
        self.cm.network_manager.find_connection = MagicMock(return_value="DUMMY_CON")
        with patch.object(logging, "warning") as mock_warning:
            result = self.cm.find_connection("DUMMY_CON_ID")
        self.assertEqual("DUMMY_CON", result)
        self.assertEqual([call("DUMMY_CON_ID")], self.cm.network_manager.find_connection.mock_calls)
        self.assertEqual(0, mock_warning.call_count)

    def test_find_device_for_connection(self):
        # not found
        self.cm.network_manager.find_device_for_connection = MagicMock(return_value=None)
        with patch.object(logging, "warning") as mock_warning:
            result = self.cm._find_device_for_connection("DUMMY_CON", "DUMMY_CON_ID")
        self.assertEqual(None, result)
        self.assertEqual([call("DUMMY_CON")], self.cm.network_manager.find_device_for_connection.mock_calls)
        self.assertEqual(1, mock_warning.call_count)

        # found
        self.cm.network_manager.find_device_for_connection = MagicMock(return_value="DUMMY_DEV")
        with patch.object(logging, "warning") as mock_warning:
            result = self.cm._find_device_for_connection("DUMMY_CON", "DUMMY_CON_ID")
        self.assertEqual("DUMMY_DEV", result)
        self.assertEqual([call("DUMMY_CON")], self.cm.network_manager.find_device_for_connection.mock_calls)
        self.assertEqual(0, mock_warning.call_count)

    def test_activate_generic_connection(self):
        # wait ok
        self.cm.network_manager.activate_connection = MagicMock(return_value="ACTIVE_CON")
        self.cm._wait_generic_connection_activation = MagicMock(return_value=True)
        self.cm.timeouts.connection_activation_timeout = datetime.timedelta(seconds=7)

        result = self.cm._activate_generic_connection("DUMMY_DEV", "DUMMY_CON")

        self.assertEqual("ACTIVE_CON", result)
        self.assertEqual(
            [call("DUMMY_CON", "DUMMY_DEV")], self.cm.network_manager.activate_connection.mock_calls
        )
        self.assertEqual(
            [call("ACTIVE_CON", self.cm.timeouts.connection_activation_timeout)],
            self.cm._wait_generic_connection_activation.mock_calls,
        )

        # wait error
        self.cm.network_manager.activate_connection = MagicMock(return_value="ACTIVE_CON")
        self.cm._wait_generic_connection_activation = MagicMock(return_value=False)
        self.cm.timeouts.connection_activation_timeout = datetime.timedelta(seconds=7)

        result = self.cm._activate_generic_connection("DUMMY_DEV", "DUMMY_CON")

        self.assertEqual(None, result)
        self.assertEqual(
            [call("DUMMY_CON", "DUMMY_DEV")], self.cm.network_manager.activate_connection.mock_calls
        )
        self.assertEqual(
            [call("ACTIVE_CON", self.cm.timeouts.connection_activation_timeout)],
            self.cm._wait_generic_connection_activation.mock_calls,
        )

    def test_now(self):
        self.assertTrue(isinstance(self.cm.now(), datetime.datetime))

    def test_wait_generic_connection_activation(self):
        dummy_con = DummyNMConnection("dummy_id", {})
        now = datetime.datetime(year=2000, month=1, day=2, hour=3, minute=4, second=5)
        timeout = datetime.timedelta(seconds=7)
        step = datetime.timedelta(seconds=1)

        # timeout
        dummy_con.get_property = MagicMock(
            return_value=connection_manager.NM_ACTIVE_CONNECTION_STATE_DEACTIVATED
        )
        self.cm.now = MagicMock(side_effect=[now, now + step, now + step + step, now + timeout + step])

        with patch.object(time, "sleep") as mock_sleep:
            result = self.cm._wait_generic_connection_activation(dummy_con, timeout)

        self.assertEqual(False, result)
        self.assertEqual([call("State"), call("State")], dummy_con.get_property.mock_calls)
        self.assertEqual([call(1), call(1)], mock_sleep.mock_calls)
        self.assertEqual([call(), call(), call(), call()], self.cm.now.mock_calls)

        # success
        dummy_con.get_property = MagicMock(
            side_effect=[
                connection_manager.NM_ACTIVE_CONNECTION_STATE_DEACTIVATED,
                connection_manager.NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
            ]
        )
        self.cm.now = MagicMock(side_effect=[now, now + step, now + step + step])

        with patch.object(time, "sleep") as mock_sleep:
            result = self.cm._wait_generic_connection_activation(dummy_con, timeout)

        self.assertEqual(True, result)
        self.assertEqual([call("State"), call("State")], dummy_con.get_property.mock_calls)
        self.assertEqual([call(1)], mock_sleep.mock_calls)
        self.assertEqual([call(), call(), call()], self.cm.now.mock_calls)

    def test_apply_sim_slot(self):
        con = DummyNMConnection("con_id", {})
        dev = DummyNMDevice()

        # default slot
        dev.get_property = MagicMock(return_value="DUMMY_UDI")
        self.cm.modem_manager.get_primary_sim_slot = MagicMock(return_value=1)
        self.cm.change_modem_sim_slot = MagicMock()

        with patch.object(connection_manager, "NM_SETTINGS_GSM_SIM_SLOT_DEFAULT", 31337):
            result = self.cm.apply_sim_slot(dev, con, 31337)

        self.assertEqual(dev, result)
        self.assertEqual([call("Udi")], dev.get_property.mock_calls)
        self.assertEqual([call("DUMMY_UDI")], self.cm.modem_manager.get_primary_sim_slot.mock_calls)
        self.assertEqual([], self.cm.change_modem_sim_slot.mock_calls)

        # current slot
        dev.get_property = MagicMock(return_value="DUMMY_UDI")
        self.cm.modem_manager.get_primary_sim_slot = MagicMock(return_value=1)
        self.cm.change_modem_sim_slot = MagicMock()

        with patch.object(connection_manager, "NM_SETTINGS_GSM_SIM_SLOT_DEFAULT", 31337):
            result = self.cm.apply_sim_slot(dev, con, 1)

        self.assertEqual(dev, result)
        self.assertEqual([call("Udi")], dev.get_property.mock_calls)
        self.assertEqual([call("DUMMY_UDI")], self.cm.modem_manager.get_primary_sim_slot.mock_calls)
        self.assertEqual([], self.cm.change_modem_sim_slot.mock_calls)

        # different slot
        dev.get_property = MagicMock(return_value="DUMMY_UDI")
        self.cm.modem_manager.get_primary_sim_slot = MagicMock(return_value=1)
        self.cm.change_modem_sim_slot = MagicMock(return_value="CHANGE_RESULT")

        with patch.object(connection_manager, "NM_SETTINGS_GSM_SIM_SLOT_DEFAULT", 31337):
            result = self.cm.apply_sim_slot(dev, con, 2)

        self.assertEqual("CHANGE_RESULT", result)
        self.assertEqual([call("Udi")], dev.get_property.mock_calls)
        self.assertEqual([call("DUMMY_UDI")], self.cm.modem_manager.get_primary_sim_slot.mock_calls)
        self.assertEqual([call(dev, con, 2)], self.cm.change_modem_sim_slot.mock_calls)

    def test_activate_gsm_connection(self):
        con = DummyNMConnection("con_id", {})
        dev = DummyNMDevice()

        # no active connection, sim not applied
        dev.get_active_connection = MagicMock(return_value=None)
        self.cm.deactivate_current_gsm_connection = MagicMock()
        con.get_sim_slot = MagicMock(return_value="dummy_slot")
        self.cm.apply_sim_slot = MagicMock(return_value=False)
        self.cm.network_manager.activate_connection = MagicMock()
        self.cm._wait_connection_activation = MagicMock()

        result = self.cm._activate_gsm_connection(dev, con)

        self.assertEqual(None, result)
        self.assertEqual([call()], dev.get_active_connection.mock_calls)
        self.assertEqual([], self.cm.deactivate_current_gsm_connection.mock_calls)
        self.assertEqual([call()], con.get_sim_slot.mock_calls)
        self.assertEqual([call(dev, con, "dummy_slot")], self.cm.apply_sim_slot.mock_calls)
        self.assertEqual([], self.cm.network_manager.activate_connection.mock_calls)
        self.assertEqual([], self.cm._wait_connection_activation.mock_calls)

        # active connection, sim applied, not activated
        dev.get_active_connection = MagicMock(return_value="old_active")
        self.cm.deactivate_current_gsm_connection = MagicMock()
        con.get_sim_slot = MagicMock(return_value="dummy_slot")
        self.cm.apply_sim_slot = MagicMock(return_value="dummy_dev_1")
        self.cm.network_manager.activate_connection = MagicMock(return_value="dummy_con_2")
        self.cm._wait_connection_activation = MagicMock(return_value=False)

        result = self.cm._activate_gsm_connection(dev, con)

        self.assertEqual(None, result)
        self.assertEqual([call()], dev.get_active_connection.mock_calls)
        self.assertEqual([call("old_active")], self.cm.deactivate_current_gsm_connection.mock_calls)
        self.assertEqual([call()], con.get_sim_slot.mock_calls)
        self.assertEqual([call(dev, con, "dummy_slot")], self.cm.apply_sim_slot.mock_calls)
        self.assertEqual([call(con, "dummy_dev_1")], self.cm.network_manager.activate_connection.mock_calls)
        self.assertEqual(
            [call("dummy_con_2", self.cm.timeouts.connection_activation_timeout)],
            self.cm._wait_connection_activation.mock_calls,
        )

        # active connection, sim applied, activated
        dev.get_active_connection = MagicMock(return_value="old_active")
        self.cm.deactivate_current_gsm_connection = MagicMock()
        con.get_sim_slot = MagicMock(return_value="dummy_slot")
        self.cm.apply_sim_slot = MagicMock(return_value="dummy_dev_1")
        self.cm.network_manager.activate_connection = MagicMock(return_value="dummy_con_2")
        self.cm._wait_connection_activation = MagicMock(return_value=True)

        result = self.cm._activate_gsm_connection(dev, con)

        self.assertEqual("dummy_con_2", result)
        self.assertEqual([call()], dev.get_active_connection.mock_calls)
        self.assertEqual([call("old_active")], self.cm.deactivate_current_gsm_connection.mock_calls)
        self.assertEqual([call()], con.get_sim_slot.mock_calls)
        self.assertEqual([call(dev, con, "dummy_slot")], self.cm.apply_sim_slot.mock_calls)
        self.assertEqual([call(con, "dummy_dev_1")], self.cm.network_manager.activate_connection.mock_calls)
        self.assertEqual(
            [call("dummy_con_2", self.cm.timeouts.connection_activation_timeout)],
            self.cm._wait_connection_activation.mock_calls,
        )
