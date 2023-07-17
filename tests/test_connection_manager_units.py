# pylint: disable=duplicate-code disable=too-many-lines
# pylint: disable=protected-access disable=attribute-defined-outside-init disable=too-few-public-methods
# pylint: disable=no-member disable=unnecessary-dunder-call disable=too-many-public-methods
import datetime
import importlib
import io
import json
import logging
import signal
import subprocess
import time
from datetime import timedelta
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

import dbus
import pycurl

from wb.nm_helper import connection_manager
from wb.nm_helper.dns_resolver import DomainNameResolveException, resolve_domain_name
from wb.nm_helper.network_manager import (
    NM_DEVICE_TYPE_ETHERNET,
    NM_DEVICE_TYPE_MODEM,
    NM_DEVICE_TYPE_WIFI,
)

# DUMMY CLASSES


class DummyNetworkManager:
    pass


class DummyNMDevice:
    pass


class DummyNMConnection:
    def __init__(self, name, settings):
        self.name = name
        self.settings = settings

    def get_connection_id(self):
        return self.name

    def get_connection_type(self):
        return "typeof_" + self.name

    def get_settings(self):
        return self.settings


class DummyNMActiveConnection:
    pass


class DummyConfigFile:
    connectivity_check_url = "DUMMY_URL"
    connectivity_check_payload = "DUMMY_PAYLOAD"
    sticky_connection_period = datetime.timedelta(seconds=123)


class DummyCurl:
    URL = 10001
    WRITEDATA = 10002
    INTERFACE = 10003
    HTTPHEADER = 10023


class DummyBytesIO:
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

    def test_load_config_01_no_tiers(self):
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

    def test_load_config_02_with_tiers(self):
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

        self.assertEqual(
            [call("high", 3, ["wb_eth0"]), call("medium", 2, ["wb_eth0"]), call("low", 1, ["wb_eth0"])],
            dummy_tier.mock_calls,
        )
        self.assertEqual(["TIER1", "TIER2", "TIER3"], output)

    def test_get_sticky_connection_period_01_default(self):
        output = self.config.get_sticky_connection_period({})

        self.assertEqual(connection_manager.DEFAULT_STICKY_CONNECTION_PERIOD, output)

    def test_get_sticky_connection_period_02_invalid(self):
        with self.assertRaises(connection_manager.ImproperlyConfigured):
            self.config.get_sticky_connection_period({"sticky_connection_period_s": "ABC"})

    def test_get_sticky_connection_period_03_valid(self):
        output = self.config.get_sticky_connection_period({"sticky_connection_period_s": 13})

        self.assertEqual(timedelta(seconds=13), output)

    def test_get_connectivity_check_url_01_http(self):
        self.assertEqual(
            "http://example",
            self.config.get_connectivity_check_url({"connectivity_check_url": "http://example"}),
        )

    def test_get_connectivity_check_url_02_https(self):
        self.assertEqual(
            "https://example",
            self.config.get_connectivity_check_url({"connectivity_check_url": "https://example"}),
        )

    def test_get_connectivity_check_url_03_error(self):
        with self.assertRaises(connection_manager.ImproperlyConfigured):
            self.config.get_connectivity_check_url({"connectivity_check_url": "example"})

    def test_get_connectivity_check_payload_01_default(self):
        self.assertEqual(
            connection_manager.DEFAULT_CONNECTIVITY_CHECK_PAYLOAD,
            self.config.get_connectivity_check_payload({}),
        )

    def test_get_connectivity_check_payload_02_invalid(self):
        with self.assertRaises(connection_manager.ImproperlyConfigured):
            self.config.get_connectivity_check_payload({"connectivity_check_payload": ""})

    def test_get_connectivity_check_payload_03_valid(self):
        self.assertEqual(
            "ABC", self.config.get_connectivity_check_payload({"connectivity_check_payload": "ABC"})
        )

    def test_has_connections_01_false(self):
        self.config.tiers = [connection_manager.ConnectionTier(name="dummy", priority=1, connections=[])]
        self.assertFalse(self.config.has_connections())

    def test_has_connections_02_true(self):
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

    def test_load_config_01_no_tiers(self):
        test_config = {"DUMMY": "CONFIG"}
        self.config.get_default_tiers = MagicMock(return_value=["DUMMY_DEFAULT_TIERS"])
        self.config.filter_out_unmanaged_connections = MagicMock()
        self.config.tiers = []

        with patch.object(connection_manager.ConfigFile, "load_config") as mock_load_config:
            self.config.load_config(test_config)

        self.assertEqual([call(test_config)], mock_load_config.mock_calls)
        self.assertEqual([call()], self.config.get_default_tiers.mock_calls)
        self.assertEqual([call()], self.config.filter_out_unmanaged_connections.mock_calls)

    def test_load_config_02_with_tiers(self):
        test_config = {"DUMMY": "CONFIG"}
        self.config.get_default_tiers = MagicMock(return_value=["DUMMY_DEFAULT_TIERS"])
        self.config.filter_out_unmanaged_connections = MagicMock()
        self.config.tiers = ["DUMMY_TIER"]

        with patch.object(connection_manager.ConfigFile, "load_config") as mock_load_config:
            self.config.load_config(test_config)

        self.assertEqual([call(test_config)], mock_load_config.mock_calls)
        self.assertEqual([], self.config.get_default_tiers.mock_calls)
        self.assertEqual([call()], self.config.filter_out_unmanaged_connections.mock_calls)

    def test_filter_out_unmanaged_connections_01_all_valid(self):
        test_tier = connection_manager.ConnectionTier(
            name="dummy", priority=1, connections=["wb_eth0", "wb_eth1", "wb_eth2"]
        )
        self.config.tiers = [test_tier]

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

    def test_filter_out_unmanaged_connections_02_has_unmanaged_cons(self):
        test_tier = connection_manager.ConnectionTier(
            name="dummy", priority=1, connections=["wb_eth0", "wb_eth1", "wb_eth2"]
        )
        self.config.tiers = [test_tier]

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

    def test_filter_out_unmanaged_connections_03_has_unfindable_cons(self):
        test_tier = connection_manager.ConnectionTier(
            name="dummy", priority=1, connections=["wb_eth0", "wb_eth1", "wb_eth2"]
        )
        self.config.tiers = [test_tier]

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
                31337,  # random invalid type
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
        with self.assertRaises(ValueError):
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
        self.timeout_man = connection_manager.TimeoutManager(connection_manager.ConfigFile())
        self.timeout_man.now = MagicMock(return_value=self.fake_now)

    def test_init(self):
        self.timeout_man = connection_manager.TimeoutManager("DUMMY_CONFIG")

        self.assertEqual("DUMMY_CONFIG", self.timeout_man.config)
        self.assertEqual({}, self.timeout_man.connection_retry_timeouts)
        self.assertEqual(None, self.timeout_man.keep_sticky_connections_until)
        self.assertEqual(
            connection_manager.CONNECTION_ACTIVATION_TIMEOUT, self.timeout_man.connection_activation_timeout
        )

    def test_now(self):
        self.timeout_man = connection_manager.TimeoutManager(connection_manager.ConfigFile())
        self.assertTrue(isinstance(self.timeout_man.now(), datetime.datetime))

    def test_debug_log_timeouts(self):
        self.timeout_man.keep_sticky_connections_until = "DUMMY1"
        self.timeout_man.connection_retry_timeouts = {"DUMMY2": 31337, "DUMMY3": 31338}

        with patch.object(logging, "debug") as mock_debug:
            self.timeout_man.debug_log_timeouts()

        self.assertEqual(
            [
                call("Sticky Connections Timeout: %s", "DUMMY1"),
                call("Connection Retry Timeout for %s: %s", "DUMMY2", 31337),
                call("Connection Retry Timeout for %s: %s", "DUMMY3", 31338),
            ],
            mock_debug.mock_calls,
        )

    def test_touch_connection_retry_timeout(self):
        self.timeout_man.touch_connection_retry_timeout("dummy_con")

        self.assertEqual([call()], self.timeout_man.now.mock_calls)
        self.assertEqual(
            {"dummy_con": self.fake_now + connection_manager.CONNECTION_ACTIVATION_RETRY_TIMEOUT},
            self.timeout_man.connection_retry_timeouts,
        )

    def test_reset_connection_retry_timeout(self):
        self.timeout_man.reset_connection_retry_timeout("dummy_con")

        self.assertEqual([call()], self.timeout_man.now.mock_calls)
        self.assertEqual({"dummy_con": self.fake_now}, self.timeout_man.connection_retry_timeouts)

    def test_touch_sticky_timeout(self):
        test_con = DummyNMConnection("dummy", {})
        test_con.get_connection_type = MagicMock(side_effect=["DEV1", "DEV2", "DEV3"])
        self.timeout_man.keep_sticky_connections_until = 31337
        self.timeout_man.config.sticky_connection_period = datetime.timedelta(seconds=1)

        with patch.object(connection_manager, "connection_type_to_device_type") as mock_ct_to_dt:
            mock_ct_to_dt.side_effect = [NM_DEVICE_TYPE_ETHERNET, NM_DEVICE_TYPE_WIFI, NM_DEVICE_TYPE_MODEM]
            self.timeout_man.touch_sticky_timeout(test_con)  # ethernet
            self.assertIsNone(self.timeout_man.keep_sticky_connections_until)
            self.timeout_man.touch_sticky_timeout(test_con)  # wifi
            self.assertEqual(
                self.fake_now + self.timeout_man.config.sticky_connection_period,
                self.timeout_man.keep_sticky_connections_until,
            )
            self.timeout_man.touch_sticky_timeout(test_con)  # modem
            self.assertEqual(
                self.fake_now + self.timeout_man.config.sticky_connection_period,
                self.timeout_man.keep_sticky_connections_until,
            )

    def test_connection_retry_timeout_is_active(self):
        self.timeout_man.connection_retry_timeouts = {}
        self.assertFalse(self.timeout_man.connection_retry_timeout_is_active("dummy_con"))

        self.timeout_man.connection_retry_timeouts = {
            "dummy_con": self.fake_now - datetime.timedelta(seconds=1)
        }
        self.assertFalse(self.timeout_man.connection_retry_timeout_is_active("dummy_con"))

        self.timeout_man.connection_retry_timeouts = {"dummy_con": self.fake_now}
        self.assertTrue(self.timeout_man.connection_retry_timeout_is_active("dummy_con"))

        self.timeout_man.connection_retry_timeouts = {
            "dummy_con": self.fake_now + datetime.timedelta(seconds=1)
        }
        self.assertTrue(self.timeout_man.connection_retry_timeout_is_active("dummy_con"))

    def test_sticky_timeout_is_active(self):
        self.timeout_man.keep_sticky_connections_until = None
        self.assertFalse(self.timeout_man.sticky_timeout_is_active())

        self.timeout_man.keep_sticky_connections_until = self.fake_now - datetime.timedelta(seconds=1)
        self.assertFalse(self.timeout_man.sticky_timeout_is_active())

        self.timeout_man.keep_sticky_connections_until = self.fake_now
        self.assertFalse(self.timeout_man.sticky_timeout_is_active())

        self.timeout_man.keep_sticky_connections_until = self.fake_now + datetime.timedelta(seconds=1)
        self.assertTrue(self.timeout_man.sticky_timeout_is_active())


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
        def dns_resolver_mock(url, _iface):
            return url

        DummyCurl.setopt = MagicMock()
        DummyCurl.perform = MagicMock()
        DummyCurl.close = MagicMock()
        DummyBytesIO.getvalue = MagicMock(return_value="ЖЖЖ".encode("UTF8"))
        with patch.object(pycurl, "Curl", DummyCurl), patch.object(io, "BytesIO", DummyBytesIO):
            output = connection_manager.curl_get("dummy_if", "dummy_url", dns_resolver_mock)
            self.assertEqual(6, DummyCurl.setopt.call_count)
            self.assertEqual(call(pycurl.Curl.URL, "dummy_url"), DummyCurl.setopt.mock_calls[0])
            self.assertEqual(2, len(DummyCurl.setopt.mock_calls[1].args))
            self.assertEqual(pycurl.Curl.WRITEDATA, DummyCurl.setopt.mock_calls[1].args[0])
            self.assertTrue(isinstance(DummyCurl.setopt.mock_calls[1].args[1], DummyBytesIO))
            self.assertEqual(call(pycurl.Curl.INTERFACE, "dummy_if"), DummyCurl.setopt.mock_calls[2])
            self.assertEqual(
                call(pycurl.CONNECTTIMEOUT, connection_manager.CONNECTIVITY_CHECK_TIMEOUT),
                DummyCurl.setopt.mock_calls[3],
            )
            self.assertEqual(
                call(pycurl.TIMEOUT, connection_manager.CONNECTIVITY_CHECK_TIMEOUT),
                DummyCurl.setopt.mock_calls[4],
            )
            self.assertEqual(
                call(pycurl.Curl.HTTPHEADER, ["Host: dummy_url"]),
                DummyCurl.setopt.mock_calls[5],
            )
            self.assertEqual([call()], DummyCurl.perform.mock_calls)
            self.assertEqual([call()], DummyCurl.close.mock_calls)
            self.assertEqual("ЖЖЖ", output)

    def test_check_connectivity_01_with_auto_config(self):
        dummy_active_cn = DummyNMActiveConnection()
        DummyConfigFile.load_config = MagicMock()
        dummy_active_cn.get_connection_id = MagicMock()
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
                call("dummy_iface1", "DUMMY_URL", resolve_domain_name),
                call("dummy_iface2", "DUMMY_URL", resolve_domain_name),
                call("dummy_iface3", "DUMMY_URL", resolve_domain_name),
            ],
            mock_curl_get.mock_calls,
        )

    def test_check_connectivity_02_with_external_config_provided(self):
        dummy_active_cn = DummyNMActiveConnection()
        DummyConfigFile.load_config = MagicMock()
        dummy_config = DummyConfigFile()
        dummy_config.connectivity_check_payload = "NEW_DUMMY_PAYLOAD"
        dummy_config.connectivity_check_url = "NEW_DUMMY_URL"
        dummy_active_cn.get_connection_id = MagicMock()
        dummy_active_cn.get_ifaces = MagicMock(side_effect=[["dummy_iface4"]])

        with patch.object(connection_manager, "curl_get") as mock_curl_get, patch.object(
            connection_manager, "read_config_json"
        ) as mock_read_config_json:
            mock_curl_get.return_value = "NEW_DUMMY_PAYLOAD"
            mock_read_config_json.return_value = {"dummy": "config"}
            result = connection_manager.check_connectivity(dummy_active_cn, config=dummy_config)

        self.assertEqual([call()], dummy_active_cn.get_ifaces.mock_calls)
        self.assertEqual([], DummyConfigFile.load_config.mock_calls)

        self.assertEqual(
            [call("dummy_iface4", "NEW_DUMMY_URL", resolve_domain_name)], mock_curl_get.mock_calls
        )
        self.assertEqual(True, result)

    def test_check_connectivity_03_dns_resolve_error(self):
        dummy_active_cn = DummyNMActiveConnection()
        dummy_active_cn.get_connection_id = MagicMock()
        dummy_active_cn.get_ifaces = MagicMock(return_value=["dummy_iface1"])
        dummy_config = DummyConfigFile()

        with patch.object(connection_manager, "curl_get") as mock_curl_get:
            mock_curl_get.side_effect = DomainNameResolveException("timeout")
            result = connection_manager.check_connectivity(dummy_active_cn, dummy_config)  # exception
            self.assertEqual(False, result)

    def test_init_logging(self):
        logger = logging.getLogger()
        logger.addFilter = MagicMock()

        with patch.object(logging, "getLogger") as mock_get_logger, patch.object(
            logging, "basicConfig"
        ) as mock_basic_config:
            connection_manager.init_logging(debug=True)
        self.assertEqual([], mock_get_logger.mock_calls)
        self.assertEqual([], logger.addFilter.mock_calls)
        self.assertEqual(
            [call(level=logging.DEBUG, format=connection_manager.LOGGING_FORMAT)],
            mock_basic_config.mock_calls,
        )

        with patch.object(logging, "getLogger") as mock_get_logger, patch.object(
            logging, "basicConfig"
        ) as mock_basic_config:
            mock_get_logger.return_value = logger
            connection_manager.init_logging(debug=False)
        self.assertEqual([call()], mock_get_logger.mock_calls)
        self.assertEqual(1, len(logger.addFilter.mock_calls))
        self.assertTrue(
            isinstance(logger.addFilter.mock_calls[0].args[0], connection_manager.ConnectionStateFilter)
        )
        self.assertEqual(
            [call(level=logging.INFO, format=connection_manager.LOGGING_FORMAT)], mock_basic_config.mock_calls
        )

    def test_replace_host_name_with_ip(self):
        def dsn_resolver_mock(url, iface):
            if iface == "wlan1":
                return url
            return "1.1.1.1"

        self.assertEqual(
            "bad_url", connection_manager.replace_host_name_with_ip("bad_url", "wlan1", dsn_resolver_mock)
        )
        self.assertEqual(
            "http://1.1.1.1/params/some",
            connection_manager.replace_host_name_with_ip(
                "http://good_url.com/params/some", "wlan2", dsn_resolver_mock
            ),
        )
        self.assertEqual(
            "http://1.1.1.1:8080/params/some",
            connection_manager.replace_host_name_with_ip(
                "http://good_url.com:8080/params/some", "wlan2", dsn_resolver_mock
            ),
        )


class ConnectionManagerTests(TestCase):
    def setUp(self) -> None:
        self.config = DummyConfigFile()
        self.con_man = connection_manager.ConnectionManager(
            config=self.config, network_manager=DummyNetworkManager(), modem_manager=DummyModemManager()
        )

    def test_current_connection_has_connectivity_01_ok(self):
        high_tier = connection_manager.ConnectionTier(name="high", priority=1, connections=["wb_eth0"])
        self.con_man.config.tiers = [high_tier]
        self.con_man.current_tier = high_tier
        self.con_man.current_connection = "wb_eth0"

        with patch.object(connection_manager, "check_connectivity", MagicMock(return_value=True)):
            self.con_man._log_connection_check_error = MagicMock()
            self.con_man.find_activated_connection = MagicMock(side_effect=["dummy_con1"])
            self.assertTrue(self.con_man.current_connection_has_connectivity())
            self.assertEqual([], self.con_man._log_connection_check_error.mock_calls)
            self.assertEqual(
                [call("dummy_con1", self.config)], connection_manager.check_connectivity.mock_calls
            )
            self.assertEqual([call("wb_eth0")], self.con_man.find_activated_connection.mock_calls)

    def test_current_connection_has_connectivity_02_no_connectivity(self):
        high_tier = connection_manager.ConnectionTier(name="high", priority=1, connections=["wb_eth0"])
        self.con_man.config.tiers = [high_tier]
        self.con_man.current_tier = high_tier
        self.con_man.current_connection = "wb_eth0"

        with patch.object(connection_manager, "check_connectivity", MagicMock(return_value=False)):
            self.con_man._log_connection_check_error = MagicMock()
            self.con_man.find_activated_connection = MagicMock(side_effect=["dummy_con2"])
            self.assertFalse(self.con_man.current_connection_has_connectivity())
            self.assertEqual([], self.con_man._log_connection_check_error.mock_calls)
            self.assertEqual(
                [call("dummy_con2", self.config)], connection_manager.check_connectivity.mock_calls
            )
            self.assertEqual([call("wb_eth0")], self.con_man.find_activated_connection.mock_calls)

    def test_current_connection_has_connectivity_03_exception(self):
        high_tier = connection_manager.ConnectionTier(name="high", priority=1, connections=["wb_eth0"])
        self.con_man.config.tiers = [high_tier]
        self.con_man.current_tier = high_tier
        self.con_man.current_connection = "wb_eth0"

        with patch.object(connection_manager, "check_connectivity", MagicMock()):
            self.con_man._log_connection_check_error = MagicMock()
            self.con_man.find_activated_connection = MagicMock(side_effect=dbus.exceptions.DBusException())
            self.assertFalse(self.con_man.current_connection_has_connectivity())
            self.assertEqual(
                [call("wb_eth0", self.con_man.find_activated_connection.side_effect)],
                self.con_man._log_connection_check_error.mock_calls,
            )
            self.assertEqual([], connection_manager.check_connectivity.mock_calls)
            self.assertEqual([call("wb_eth0")], self.con_man.find_activated_connection.mock_calls)

    def test_check_non_current_connection_01_skip_current(self):
        high_tier = connection_manager.ConnectionTier(name="high", priority=1, connections=["wb_eth0"])
        self.con_man.config.tiers = [high_tier]
        self.con_man.current_tier = high_tier
        self.con_man.current_connection = "wb_eth0"

        with patch.object(connection_manager, "check_connectivity", MagicMock()):
            self.con_man._log_connection_check_error = MagicMock()
            self.con_man.ok_to_activate_connection = MagicMock()
            self.con_man.find_activated_connection = MagicMock()
            self.con_man.activate_connection = MagicMock()
            self.con_man.timeouts.touch_connection_retry_timeout = MagicMock()
            self.assertFalse(self.con_man.non_current_connection_has_connectivity(high_tier, "wb_eth0"))
            self.assertEqual([], self.con_man._log_connection_check_error.mock_calls)
            self.assertEqual([], connection_manager.check_connectivity.mock_calls)
            self.assertEqual([], self.con_man.find_activated_connection.mock_calls)
            self.assertEqual([], self.con_man.activate_connection.mock_calls)
            self.assertEqual([], self.con_man.ok_to_activate_connection.mock_calls)
            self.assertEqual([], self.con_man.timeouts.touch_connection_retry_timeout.mock_calls)

    def test_check_non_current_connection_02_exception(self):
        high_tier = connection_manager.ConnectionTier(name="high", priority=1, connections=["wb_eth0"])
        self.con_man.config.tiers = [high_tier]
        self.con_man.current_tier = high_tier
        self.con_man.current_connection = "wb_eth0"

        with patch.object(connection_manager, "check_connectivity", MagicMock()):
            self.con_man._log_connection_check_error = MagicMock()
            self.con_man.ok_to_activate_connection = MagicMock()
            self.con_man.find_activated_connection = MagicMock(side_effect=dbus.exceptions.DBusException())
            self.con_man.activate_connection = MagicMock()
            self.con_man.timeouts.touch_connection_retry_timeout = MagicMock()
            self.assertFalse(self.con_man.non_current_connection_has_connectivity(high_tier, "wb_eth1"))
            self.assertEqual(
                [call("wb_eth1", self.con_man.find_activated_connection.side_effect)],
                self.con_man._log_connection_check_error.mock_calls,
            )
            self.assertEqual([], connection_manager.check_connectivity.mock_calls)
            self.assertEqual([call("wb_eth1")], self.con_man.find_activated_connection.mock_calls)
            self.assertEqual([], self.con_man.activate_connection.mock_calls)
            self.assertEqual([], self.con_man.ok_to_activate_connection.mock_calls)
            self.assertEqual(
                [call("wb_eth1")], self.con_man.timeouts.touch_connection_retry_timeout.mock_calls
            )

    def test_check_non_current_connection_03_active_and_has_connectivity(self):
        high_tier = connection_manager.ConnectionTier(name="high", priority=1, connections=["wb_eth0"])
        self.con_man.config.tiers = [high_tier]
        self.con_man.current_tier = high_tier
        self.con_man.current_connection = "wb_eth0"

        with patch.object(connection_manager, "check_connectivity", MagicMock(return_value=True)):
            self.con_man._log_connection_check_error = MagicMock()
            self.con_man.ok_to_activate_connection = MagicMock()
            self.con_man.find_activated_connection = MagicMock(return_value="dev1")
            self.con_man.activate_connection = MagicMock()
            self.con_man.timeouts.touch_connection_retry_timeout = MagicMock()
            self.assertTrue(self.con_man.non_current_connection_has_connectivity(high_tier, "wb_eth1"))
            self.assertEqual([call("wb_eth1")], self.con_man.find_activated_connection.mock_calls)
            self.assertEqual([call("dev1", self.config)], connection_manager.check_connectivity.mock_calls)
            self.assertEqual([], self.con_man.activate_connection.mock_calls)
            self.assertEqual([], self.con_man.ok_to_activate_connection.mock_calls)
            self.assertEqual([], self.con_man.timeouts.touch_connection_retry_timeout.mock_calls)
            self.assertEqual([], self.con_man._log_connection_check_error.mock_calls)

    def test_check_non_current_connection_04_not_active_not_activated_and_has_connectivity(self):
        high_tier = connection_manager.ConnectionTier(name="high", priority=1, connections=["wb_eth0"])
        self.con_man.config.tiers = [high_tier]
        self.con_man.current_tier = high_tier
        self.con_man.current_connection = "wb_eth0"

        with patch.object(connection_manager, "check_connectivity", MagicMock(return_value=True)):
            self.con_man._log_connection_check_error = MagicMock()
            self.con_man.ok_to_activate_connection = MagicMock(return_value=True)
            self.con_man.find_activated_connection = MagicMock(return_value=None)
            self.con_man.activate_connection = MagicMock(return_value="dev1")
            self.con_man.timeouts.touch_connection_retry_timeout = MagicMock()
            self.assertTrue(self.con_man.non_current_connection_has_connectivity(high_tier, "wb_eth1"))
            self.assertEqual([call("wb_eth1")], self.con_man.find_activated_connection.mock_calls)
            self.assertEqual([call("wb_eth1")], self.con_man.ok_to_activate_connection.mock_calls)
            self.assertEqual([call("wb_eth1")], self.con_man.activate_connection.mock_calls)
            self.assertEqual([call("dev1", self.config)], connection_manager.check_connectivity.mock_calls)
            self.assertEqual(
                [call("wb_eth1")], self.con_man.timeouts.touch_connection_retry_timeout.mock_calls
            )
            self.assertEqual([], self.con_man._log_connection_check_error.mock_calls)

    def test_check_non_current_connection_05_not_active_not_ok_to_activate(self):
        high_tier = connection_manager.ConnectionTier(name="high", priority=1, connections=["wb_eth0"])
        self.con_man.config.tiers = [high_tier]
        self.con_man.current_tier = high_tier
        self.con_man.current_connection = "wb_eth0"

        with patch.object(connection_manager, "check_connectivity", MagicMock()):
            self.con_man._log_connection_check_error = MagicMock()
            self.con_man.ok_to_activate_connection = MagicMock(return_value=False)
            self.con_man.find_activated_connection = MagicMock(return_value=None)
            self.con_man.activate_connection = MagicMock(return_value="dev1")
            self.con_man.timeouts.touch_connection_retry_timeout = MagicMock()
            self.assertFalse(self.con_man.non_current_connection_has_connectivity(high_tier, "wb_eth1"))
            self.assertEqual([call("wb_eth1")], self.con_man.find_activated_connection.mock_calls)
            self.assertEqual([call("wb_eth1")], self.con_man.ok_to_activate_connection.mock_calls)
            self.assertEqual([], self.con_man.activate_connection.mock_calls)
            self.assertEqual([], connection_manager.check_connectivity.mock_calls)
            self.assertEqual([], self.con_man.timeouts.touch_connection_retry_timeout.mock_calls)
            self.assertEqual([], self.con_man._log_connection_check_error.mock_calls)

    def test_check_non_current_connection_06_not_active_failed_to_activate(self):
        high_tier = connection_manager.ConnectionTier(name="high", priority=1, connections=["wb_eth0"])
        self.con_man.config.tiers = [high_tier]
        self.con_man.current_tier = high_tier
        self.con_man.current_connection = "wb_eth0"

        with patch.object(connection_manager, "check_connectivity", MagicMock()):
            self.con_man._log_connection_check_error = MagicMock()
            self.con_man.ok_to_activate_connection = MagicMock(return_value=True)
            self.con_man.find_activated_connection = MagicMock(return_value=None)
            self.con_man.activate_connection = MagicMock(return_value=None)
            self.con_man.timeouts.touch_connection_retry_timeout = MagicMock()
            self.assertFalse(self.con_man.non_current_connection_has_connectivity(high_tier, "wb_eth1"))
            self.assertEqual([call("wb_eth1")], self.con_man.find_activated_connection.mock_calls)
            self.assertEqual([call("wb_eth1")], self.con_man.ok_to_activate_connection.mock_calls)
            self.assertEqual([call("wb_eth1")], self.con_man.activate_connection.mock_calls)
            self.assertEqual([], connection_manager.check_connectivity.mock_calls)
            self.assertEqual(
                [call("wb_eth1")], self.con_man.timeouts.touch_connection_retry_timeout.mock_calls
            )
            self.assertEqual([], self.con_man._log_connection_check_error.mock_calls)

    def test_check_01_curent_is_ok(self):
        high_tier = connection_manager.ConnectionTier(name="high", priority=1, connections=["wb_eth0"])
        low_tier = connection_manager.ConnectionTier(name="low", priority=3, connections=["wb_wifi_client"])
        self.con_man.config.tiers = [high_tier, low_tier]
        self.con_man.current_tier = high_tier
        self.con_man.current_connection = "wb_eth0"

        self.con_man.timeouts.debug_log_timeouts = MagicMock()
        self.con_man.current_connection_has_connectivity = MagicMock(return_value=True)
        self.con_man.non_current_connection_has_connectivity = MagicMock()
        self.assertEqual((high_tier, "wb_eth0"), self.con_man.check())
        self.assertEqual([call()], self.con_man.current_connection_has_connectivity.mock_calls)
        self.assertEqual([], self.con_man.non_current_connection_has_connectivity.mock_calls)

    def test_check_02_non_current_is_ok(self):
        high_tier = connection_manager.ConnectionTier(name="high", priority=1, connections=["wb_eth0"])
        low_tier = connection_manager.ConnectionTier(name="low", priority=3, connections=["wb_wifi_client"])
        self.con_man.config.tiers = [high_tier, low_tier]
        self.con_man.current_tier = high_tier
        self.con_man.current_connection = "wb_eth0"

        self.con_man.timeouts.debug_log_timeouts = MagicMock()
        self.con_man.current_connection_has_connectivity = MagicMock(return_value=False)
        self.con_man.non_current_connection_has_connectivity = MagicMock(side_effect=[False, True])
        self.assertEqual((low_tier, "wb_wifi_client"), self.con_man.check())
        self.assertEqual([call()], self.con_man.current_connection_has_connectivity.mock_calls)
        self.assertEqual(
            [call(high_tier, "wb_eth0"), call(low_tier, "wb_wifi_client")],
            self.con_man.non_current_connection_has_connectivity.mock_calls,
        )

    def test_check_03_everything_is_down(self):
        high_tier = connection_manager.ConnectionTier(name="high", priority=1, connections=["wb_eth0"])
        low_tier = connection_manager.ConnectionTier(name="low", priority=3, connections=["wb_wifi_client"])
        self.con_man.config.tiers = [high_tier, low_tier]
        self.con_man.current_tier = high_tier
        self.con_man.current_connection = "wb_eth0"

        self.con_man.timeouts.debug_log_timeouts = MagicMock()
        self.con_man.current_connection_has_connectivity = MagicMock(return_value=False)
        self.con_man.non_current_connection_has_connectivity = MagicMock(side_effect=[False, False])
        self.assertEqual((high_tier, "wb_eth0"), self.con_man.check())
        self.assertEqual([call()], self.con_man.current_connection_has_connectivity.mock_calls)
        self.assertEqual(
            [call(high_tier, "wb_eth0"), call(low_tier, "wb_wifi_client")],
            self.con_man.non_current_connection_has_connectivity.mock_calls,
        )

    def test_log_connection_check_error(self):
        with patch.object(logging, "warning") as mock_warning:
            ex = Exception("Test")
            self.con_man._log_connection_check_error("wb_eth3", ex)
        self.assertEqual(1, mock_warning.call_count)

    def test_activate_connection_01_con_not_found(self):
        dummy_con = DummyNMConnection("wb_eth6", {})
        dummy_con.get_connection_type = MagicMock()
        self.con_man.find_connection = MagicMock(return_value=None)
        self.con_man._find_device_for_connection = MagicMock()
        self.con_man._activate_connection_with_type = MagicMock()
        with patch.object(connection_manager, "connection_type_to_device_type") as dummy_ct_to_dt:
            result = self.con_man.activate_connection("wb_eth6")
        self.assertEqual(None, result)
        self.assertEqual([call("wb_eth6")], self.con_man.find_connection.mock_calls)
        self.assertEqual([], self.con_man._find_device_for_connection.mock_calls)
        self.assertEqual([], dummy_ct_to_dt.mock_calls)
        self.assertEqual([], self.con_man._activate_connection_with_type.mock_calls)

    def test_activate_connection_02_dev_not_found(self):
        dummy_con = DummyNMConnection("wb_eth6", {})
        dummy_con.get_connection_type = MagicMock()
        self.con_man.find_connection = MagicMock(return_value=dummy_con)
        self.con_man._find_device_for_connection = MagicMock(return_value=None)
        self.con_man._activate_connection_with_type = MagicMock()
        with patch.object(connection_manager, "connection_type_to_device_type") as dummy_ct_to_dt:
            result = self.con_man.activate_connection("wb_eth6")
        self.assertEqual(None, result)
        self.assertEqual([call("wb_eth6")], self.con_man.find_connection.mock_calls)
        self.assertEqual([call(dummy_con, "wb_eth6")], self.con_man._find_device_for_connection.mock_calls)
        self.assertEqual([], dummy_ct_to_dt.mock_calls)
        self.assertEqual([], self.con_man._activate_connection_with_type.mock_calls)

    def test_activate_connection_03_success(self):
        dummy_con = DummyNMConnection("wb_eth6", {})
        dummy_con.get_connection_type = MagicMock(return_value="DUMMY_CON_TYPE")
        self.con_man.find_connection = MagicMock(return_value=dummy_con)
        self.con_man._find_device_for_connection = MagicMock(return_value="DUMMY_DEV")
        self.con_man._activate_connection_with_type = MagicMock(return_value="ACTIVATION_RESULT")
        with patch.object(connection_manager, "connection_type_to_device_type") as dummy_ct_to_dt:
            dummy_ct_to_dt.return_value = "DUMMY_DEV_TYPE"
            result = self.con_man.activate_connection("wb_eth6")
        self.assertEqual("ACTIVATION_RESULT", result)
        self.assertEqual([call("wb_eth6")], self.con_man.find_connection.mock_calls)
        self.assertEqual([call(dummy_con, "wb_eth6")], self.con_man._find_device_for_connection.mock_calls)
        self.assertEqual([call("DUMMY_CON_TYPE")], dummy_ct_to_dt.mock_calls)
        self.assertEqual(
            [call("DUMMY_DEV", dummy_con, "DUMMY_DEV_TYPE", "wb_eth6")],
            self.con_man._activate_connection_with_type.mock_calls,
        )

    def test_activate_connection_with_type_01_ethernet(self):
        self.con_man._activate_generic_connection = MagicMock(return_value="ETH_RESULT")
        self.con_man._activate_wifi_connection = MagicMock()
        self.con_man._activate_gsm_connection = MagicMock()
        result = self.con_man._activate_connection_with_type(
            "DUMMY_DEV", "DUMMY_CON", NM_DEVICE_TYPE_ETHERNET, "CON_ID"
        )
        self.assertEqual("ETH_RESULT", result)
        self.assertEqual(
            [call.__bool__(), call("DUMMY_DEV", "DUMMY_CON")],
            self.con_man._activate_generic_connection.mock_calls,
        )
        self.assertEqual([], self.con_man._activate_wifi_connection.mock_calls)
        self.assertEqual([], self.con_man._activate_gsm_connection.mock_calls)

    def test_activate_connection_with_type_02_wifi(self):
        self.con_man._activate_generic_connection = MagicMock()
        self.con_man._activate_wifi_connection = MagicMock(return_value="WIFI_RESULT")
        self.con_man._activate_gsm_connection = MagicMock()
        result = self.con_man._activate_connection_with_type(
            "DUMMY_DEV", "DUMMY_CON", NM_DEVICE_TYPE_WIFI, "CON_ID"
        )
        self.assertEqual("WIFI_RESULT", result)
        self.assertEqual([], self.con_man._activate_generic_connection.mock_calls)
        self.assertEqual(
            [call.__bool__(), call("DUMMY_DEV", "DUMMY_CON")],
            self.con_man._activate_wifi_connection.mock_calls,
        )
        self.assertEqual([], self.con_man._activate_gsm_connection.mock_calls)

    def test_activate_connection_with_type_03_modem(self):
        self.con_man._activate_generic_connection = MagicMock()
        self.con_man._activate_wifi_connection = MagicMock()
        self.con_man._activate_gsm_connection = MagicMock(return_value="MODEM_RESULT")
        result = self.con_man._activate_connection_with_type(
            "DUMMY_DEV", "DUMMY_CON", NM_DEVICE_TYPE_MODEM, "CON_ID"
        )
        self.assertEqual("MODEM_RESULT", result)
        self.assertEqual([], self.con_man._activate_generic_connection.mock_calls)
        self.assertEqual([], self.con_man._activate_wifi_connection.mock_calls)
        self.assertEqual(
            [call.__bool__(), call("DUMMY_DEV", "DUMMY_CON")],
            self.con_man._activate_gsm_connection.mock_calls,
        )

    def test_activate_connection_with_type_04_unknown(self):
        self.con_man._activate_generic_connection = MagicMock()
        self.con_man._activate_wifi_connection = MagicMock()
        self.con_man._activate_gsm_connection = MagicMock()
        with patch.object(logging, "warning") as mock_warning:
            result = self.con_man._activate_connection_with_type("DUMMY_DEV", "DUMMY_CON", 31337, "CON_ID")
        self.assertEqual(None, result)
        self.assertEqual([], self.con_man._activate_generic_connection.mock_calls)
        self.assertEqual([], self.con_man._activate_wifi_connection.mock_calls)
        self.assertEqual([], self.con_man._activate_gsm_connection.mock_calls)
        self.assertEqual(1, mock_warning.call_count)

    def test_find_connection_01_not_found(self):
        self.con_man.network_manager.find_connection = MagicMock(return_value=None)
        with patch.object(logging, "warning") as mock_warning:
            result = self.con_man.find_connection("DUMMY_CON")
        self.assertEqual(None, result)
        self.assertEqual([call("DUMMY_CON")], self.con_man.network_manager.find_connection.mock_calls)
        self.assertEqual(1, mock_warning.call_count)

    def test_find_connection_02_found(self):
        self.con_man.network_manager.find_connection = MagicMock(return_value="DUMMY_CON")
        with patch.object(logging, "warning") as mock_warning:
            result = self.con_man.find_connection("DUMMY_CON_ID")
        self.assertEqual("DUMMY_CON", result)
        self.assertEqual([call("DUMMY_CON_ID")], self.con_man.network_manager.find_connection.mock_calls)
        self.assertEqual(0, mock_warning.call_count)

    def test_find_device_for_connection_01_not_found(self):
        self.con_man.network_manager.find_device_for_connection = MagicMock(return_value=None)
        with patch.object(logging, "warning") as mock_warning:
            result = self.con_man._find_device_for_connection("DUMMY_CON", "DUMMY_CON_ID")
        self.assertEqual(None, result)
        self.assertEqual(
            [call("DUMMY_CON")], self.con_man.network_manager.find_device_for_connection.mock_calls
        )
        self.assertEqual(1, mock_warning.call_count)

    def test_find_device_for_connection_02_found(self):
        self.con_man.network_manager.find_device_for_connection = MagicMock(return_value="DUMMY_DEV")
        with patch.object(logging, "warning") as mock_warning:
            result = self.con_man._find_device_for_connection("DUMMY_CON", "DUMMY_CON_ID")
        self.assertEqual("DUMMY_DEV", result)
        self.assertEqual(
            [call("DUMMY_CON")], self.con_man.network_manager.find_device_for_connection.mock_calls
        )
        self.assertEqual(0, mock_warning.call_count)

    def test_activate_generic_connection_01_wait_ok(self):
        self.con_man.network_manager.activate_connection = MagicMock(return_value="ACTIVE_CON")
        self.con_man._wait_generic_connection_activation = MagicMock(return_value=True)
        self.con_man.timeouts.connection_activation_timeout = datetime.timedelta(seconds=7)

        result = self.con_man._activate_generic_connection("DUMMY_DEV", "DUMMY_CON")

        self.assertEqual("ACTIVE_CON", result)
        self.assertEqual(
            [call("DUMMY_CON", "DUMMY_DEV")], self.con_man.network_manager.activate_connection.mock_calls
        )
        self.assertEqual(
            [call("ACTIVE_CON", self.con_man.timeouts.connection_activation_timeout)],
            self.con_man._wait_generic_connection_activation.mock_calls,
        )

    def test_activate_generic_connection_02_wait_error(self):
        self.con_man.network_manager.activate_connection = MagicMock(return_value="ACTIVE_CON")
        self.con_man._wait_generic_connection_activation = MagicMock(return_value=False)
        self.con_man.timeouts.connection_activation_timeout = datetime.timedelta(seconds=7)

        result = self.con_man._activate_generic_connection("DUMMY_DEV", "DUMMY_CON")

        self.assertEqual(None, result)
        self.assertEqual(
            [call("DUMMY_CON", "DUMMY_DEV")], self.con_man.network_manager.activate_connection.mock_calls
        )
        self.assertEqual(
            [call("ACTIVE_CON", self.con_man.timeouts.connection_activation_timeout)],
            self.con_man._wait_generic_connection_activation.mock_calls,
        )

    def test_now(self):
        self.assertTrue(isinstance(self.con_man.now(), datetime.datetime))

    def test_wait_generic_connection_activation_01_timeout(self):
        dummy_con = DummyNMConnection("dummy_id", {})
        now = datetime.datetime(year=2000, month=1, day=2, hour=3, minute=4, second=5)
        timeout = datetime.timedelta(seconds=7)
        step = datetime.timedelta(seconds=1)
        dummy_con.get_property = MagicMock(
            return_value=connection_manager.NM_ACTIVE_CONNECTION_STATE_DEACTIVATED
        )
        self.con_man.now = MagicMock(side_effect=[now, now + step, now + step + step, now + timeout + step])

        with patch.object(time, "sleep") as mock_sleep:
            result = self.con_man._wait_generic_connection_activation(dummy_con, timeout)

        self.assertEqual(False, result)
        self.assertEqual([call("State"), call("State")], dummy_con.get_property.mock_calls)
        self.assertEqual([call(1), call(1)], mock_sleep.mock_calls)
        self.assertEqual([call(), call(), call(), call()], self.con_man.now.mock_calls)

    def test_wait_generic_connection_activation_02_success(self):
        dummy_con = DummyNMConnection("dummy_id", {})
        now = datetime.datetime(year=2000, month=1, day=2, hour=3, minute=4, second=5)
        timeout = datetime.timedelta(seconds=7)
        step = datetime.timedelta(seconds=1)
        dummy_con.get_property = MagicMock(
            side_effect=[
                connection_manager.NM_ACTIVE_CONNECTION_STATE_DEACTIVATED,
                connection_manager.NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
            ]
        )
        self.con_man.now = MagicMock(side_effect=[now, now + step, now + step + step])

        with patch.object(time, "sleep") as mock_sleep:
            result = self.con_man._wait_generic_connection_activation(dummy_con, timeout)

        self.assertEqual(True, result)
        self.assertEqual([call("State"), call("State")], dummy_con.get_property.mock_calls)
        self.assertEqual([call(1)], mock_sleep.mock_calls)
        self.assertEqual([call(), call(), call()], self.con_man.now.mock_calls)

    def test_apply_sim_slot_01_default_slot(self):
        con = DummyNMConnection("con_id", {})
        dev = DummyNMDevice()
        dev.get_property = MagicMock(return_value="DUMMY_UDI")
        self.con_man.modem_manager.get_primary_sim_slot = MagicMock(return_value=1)
        self.con_man.change_modem_sim_slot = MagicMock()

        with patch.object(connection_manager, "NM_SETTINGS_GSM_SIM_SLOT_DEFAULT", 31337):
            result = self.con_man.apply_sim_slot(dev, con, 31337)

        self.assertEqual(dev, result)
        self.assertEqual([call("Udi")], dev.get_property.mock_calls)
        self.assertEqual([call("DUMMY_UDI")], self.con_man.modem_manager.get_primary_sim_slot.mock_calls)
        self.assertEqual([], self.con_man.change_modem_sim_slot.mock_calls)

    def test_apply_sim_slot_02_current_slot(self):
        con = DummyNMConnection("con_id", {})
        dev = DummyNMDevice()
        dev.get_property = MagicMock(return_value="DUMMY_UDI")
        self.con_man.modem_manager.get_primary_sim_slot = MagicMock(return_value=1)
        self.con_man.change_modem_sim_slot = MagicMock()

        with patch.object(connection_manager, "NM_SETTINGS_GSM_SIM_SLOT_DEFAULT", 31337):
            result = self.con_man.apply_sim_slot(dev, con, 1)

        self.assertEqual(dev, result)
        self.assertEqual([call("Udi")], dev.get_property.mock_calls)
        self.assertEqual([call("DUMMY_UDI")], self.con_man.modem_manager.get_primary_sim_slot.mock_calls)
        self.assertEqual([], self.con_man.change_modem_sim_slot.mock_calls)

    def test_apply_sim_slot_03_different_slot(self):
        con = DummyNMConnection("con_id", {})
        dev = DummyNMDevice()
        dev.get_property = MagicMock(return_value="DUMMY_UDI")
        self.con_man.modem_manager.get_primary_sim_slot = MagicMock(return_value=1)
        self.con_man.change_modem_sim_slot = MagicMock(return_value="CHANGE_RESULT")

        with patch.object(connection_manager, "NM_SETTINGS_GSM_SIM_SLOT_DEFAULT", 31337):
            result = self.con_man.apply_sim_slot(dev, con, 2)

        self.assertEqual("CHANGE_RESULT", result)
        self.assertEqual([call("Udi")], dev.get_property.mock_calls)
        self.assertEqual([call("DUMMY_UDI")], self.con_man.modem_manager.get_primary_sim_slot.mock_calls)
        self.assertEqual([call(dev, con, 2)], self.con_man.change_modem_sim_slot.mock_calls)

    def test_activate_gsm_connection_01_no_active_cn_sim_not_applied(self):
        con = DummyNMConnection("con_id", {})
        dev = DummyNMDevice()
        dev.get_active_connection = MagicMock(return_value=None)
        self.con_man.deactivate_current_gsm_connection = MagicMock()
        con.get_sim_slot = MagicMock(return_value="dummy_slot")
        self.con_man.apply_sim_slot = MagicMock(return_value=False)
        self.con_man.network_manager.activate_connection = MagicMock()
        self.con_man._wait_connection_activation = MagicMock()

        result = self.con_man._activate_gsm_connection(dev, con)

        self.assertEqual(None, result)
        self.assertEqual([call()], dev.get_active_connection.mock_calls)
        self.assertEqual([], self.con_man.deactivate_current_gsm_connection.mock_calls)
        self.assertEqual([call()], con.get_sim_slot.mock_calls)
        self.assertEqual([call(dev, con, "dummy_slot")], self.con_man.apply_sim_slot.mock_calls)
        self.assertEqual([], self.con_man.network_manager.activate_connection.mock_calls)
        self.assertEqual([], self.con_man._wait_connection_activation.mock_calls)

    def test_activate_gsm_connection_02_active_cn_sim_applied_not_activated(self):
        con = DummyNMConnection("con_id", {})
        dev = DummyNMDevice()
        dev.get_active_connection = MagicMock(return_value="old_active")
        self.con_man.deactivate_current_gsm_connection = MagicMock()
        con.get_sim_slot = MagicMock(return_value="dummy_slot")
        self.con_man.apply_sim_slot = MagicMock(return_value="dummy_dev_1")
        self.con_man.network_manager.activate_connection = MagicMock(return_value="dummy_con_2")
        self.con_man._wait_connection_activation = MagicMock(return_value=False)

        result = self.con_man._activate_gsm_connection(dev, con)

        self.assertEqual(None, result)
        self.assertEqual([call()], dev.get_active_connection.mock_calls)
        self.assertEqual([call("old_active")], self.con_man.deactivate_current_gsm_connection.mock_calls)
        self.assertEqual([call()], con.get_sim_slot.mock_calls)
        self.assertEqual([call(dev, con, "dummy_slot")], self.con_man.apply_sim_slot.mock_calls)
        self.assertEqual(
            [call(con, "dummy_dev_1")], self.con_man.network_manager.activate_connection.mock_calls
        )
        self.assertEqual(
            [call("dummy_con_2", self.con_man.timeouts.connection_activation_timeout)],
            self.con_man._wait_connection_activation.mock_calls,
        )

    def test_activate_gsm_connection_03_active_cn_sim_applied_activated(self):
        con = DummyNMConnection("con_id", {})
        dev = DummyNMDevice()
        dev.get_active_connection = MagicMock(return_value="old_active")
        self.con_man.deactivate_current_gsm_connection = MagicMock()
        con.get_sim_slot = MagicMock(return_value="dummy_slot")
        self.con_man.apply_sim_slot = MagicMock(return_value="dummy_dev_1")
        self.con_man.network_manager.activate_connection = MagicMock(return_value="dummy_con_2")
        self.con_man._wait_connection_activation = MagicMock(return_value=True)

        result = self.con_man._activate_gsm_connection(dev, con)

        self.assertEqual("dummy_con_2", result)
        self.assertEqual([call()], dev.get_active_connection.mock_calls)
        self.assertEqual([call("old_active")], self.con_man.deactivate_current_gsm_connection.mock_calls)
        self.assertEqual([call()], con.get_sim_slot.mock_calls)
        self.assertEqual([call(dev, con, "dummy_slot")], self.con_man.apply_sim_slot.mock_calls)
        self.assertEqual(
            [call(con, "dummy_dev_1")], self.con_man.network_manager.activate_connection.mock_calls
        )
        self.assertEqual(
            [call("dummy_con_2", self.con_man.timeouts.connection_activation_timeout)],
            self.con_man._wait_connection_activation.mock_calls,
        )

    def test_activate_wifi_connection_01_no_active_cn_not_activated(self):
        self.con_man._get_active_wifi_connections = MagicMock(return_value=[])
        self.con_man.deactivate_connection = MagicMock()
        self.con_man.network_manager.activate_connection = MagicMock(return_value="NEW_CON")
        self.con_man._wait_connection_activation = MagicMock(return_value=False)

        result = self.con_man._activate_wifi_connection("DUMMY_DEV", "DUMMY_CON")

        self.assertEqual([call()], self.con_man._get_active_wifi_connections.mock_calls)
        self.assertEqual([], self.con_man.deactivate_connection.mock_calls)
        self.assertEqual(
            [call("DUMMY_CON", "DUMMY_DEV")], self.con_man.network_manager.activate_connection.mock_calls
        )
        self.assertEqual(
            [call("NEW_CON", self.con_man.timeouts.connection_activation_timeout)],
            self.con_man._wait_connection_activation.mock_calls,
        )
        self.assertEqual(None, result)

    def test_activate_wifi_connection_02_active_cn_activated(self):
        active_cn = DummyNMActiveConnection()
        active_cn.get_connection_id = MagicMock()
        self.con_man._get_active_wifi_connections = MagicMock(return_value=[active_cn])
        self.con_man.deactivate_connection = MagicMock()
        self.con_man.network_manager.activate_connection = MagicMock(return_value="NEW_CON")
        self.con_man._wait_connection_activation = MagicMock(return_value=True)

        result = self.con_man._activate_wifi_connection("DUMMY_DEV", "DUMMY_CON")

        self.assertEqual([call()], self.con_man._get_active_wifi_connections.mock_calls)
        self.assertEqual([call(active_cn)], self.con_man.deactivate_connection.mock_calls)
        self.assertEqual(
            [call("DUMMY_CON", "DUMMY_DEV")], self.con_man.network_manager.activate_connection.mock_calls
        )
        self.assertEqual(
            [call("NEW_CON", self.con_man.timeouts.connection_activation_timeout)],
            self.con_man._wait_connection_activation.mock_calls,
        )
        self.assertEqual("NEW_CON", result)

    def test_deactivate_connection_01_current_con(self):
        active_cn = DummyNMActiveConnection()
        active_cn.get_connection_id = MagicMock(return_value="DUMMY_CON")
        self.con_man.network_manager.deactivate_connection = MagicMock()
        self.con_man._wait_connection_deactivation = MagicMock()
        self.con_man.current_connection = "DUMMY_CON"
        self.con_man.current_tier = "DUMMY_TIER"

        self.con_man.deactivate_connection(active_cn)

        self.assertEqual([call()], active_cn.get_connection_id.mock_calls)
        self.assertEqual([call(active_cn)], self.con_man.network_manager.deactivate_connection.mock_calls)
        self.assertEqual(
            [call(active_cn, self.con_man.timeouts.connection_activation_timeout)],
            self.con_man._wait_connection_deactivation.mock_calls,
        )
        self.assertEqual(None, self.con_man.current_connection)
        self.assertEqual(None, self.con_man.current_tier)

    def test_deactivate_connection_02_non_current_con(self):
        active_cn = DummyNMActiveConnection()
        active_cn.get_connection_id = MagicMock(return_value="DUMMY_CON")
        self.con_man.network_manager.deactivate_connection = MagicMock()
        self.con_man._wait_connection_deactivation = MagicMock()
        self.con_man.current_connection = "DUMMY_OTHER_CON"
        self.con_man.current_tier = "DUMMY_TIER"

        self.con_man.deactivate_connection(active_cn)

        self.assertEqual([call()], active_cn.get_connection_id.mock_calls)
        self.assertEqual([call(active_cn)], self.con_man.network_manager.deactivate_connection.mock_calls)
        self.assertEqual(
            [call(active_cn, self.con_man.timeouts.connection_activation_timeout)],
            self.con_man._wait_connection_deactivation.mock_calls,
        )
        self.assertEqual("DUMMY_OTHER_CON", self.con_man.current_connection)
        self.assertEqual("DUMMY_TIER", self.con_man.current_tier)

    def test_change_modem_sim_slot_01_slot_not_set(self):
        dev = DummyNMDevice()
        dev.get_property = MagicMock(return_value="DUMMY_PATH")
        self.con_man.modem_manager.set_primary_sim_slot = MagicMock(return_value=False)
        self.con_man._wait_gsm_sim_slot_to_change = MagicMock()

        result = self.con_man.change_modem_sim_slot(dev, "DUMMY_CON", 2)

        self.assertEqual([call("Udi")], dev.get_property.mock_calls)
        self.assertEqual([call("DUMMY_PATH", 2)], self.con_man.modem_manager.set_primary_sim_slot.mock_calls)
        self.assertEqual([], self.con_man._wait_gsm_sim_slot_to_change.mock_calls)
        self.assertEqual(None, result)

    def test_change_modem_sim_slot_02_wait_failed(self):
        dev = DummyNMDevice()
        dev.get_property = MagicMock(return_value="DUMMY_PATH")
        self.con_man.modem_manager.set_primary_sim_slot = MagicMock(return_value=True)
        self.con_man._wait_gsm_sim_slot_to_change = MagicMock(return_value=None)

        result = self.con_man.change_modem_sim_slot(dev, "DUMMY_CON", 2)

        self.assertEqual([call("Udi")], dev.get_property.mock_calls)
        self.assertEqual([call("DUMMY_PATH", 2)], self.con_man.modem_manager.set_primary_sim_slot.mock_calls)
        self.assertEqual(
            [call("DUMMY_CON", "2", self.con_man.timeouts.connection_activation_timeout)],
            self.con_man._wait_gsm_sim_slot_to_change.mock_calls,
        )
        self.assertEqual(None, result)

    def test_change_modem_sim_slot_03_success(self):
        dev = DummyNMDevice()
        dev.get_property = MagicMock(return_value="DUMMY_PATH")
        self.con_man.modem_manager.set_primary_sim_slot = MagicMock(return_value=True)
        self.con_man._wait_gsm_sim_slot_to_change = MagicMock(return_value="DUMMY_DEV")

        result = self.con_man.change_modem_sim_slot(dev, "DUMMY_CON", 2)

        self.assertEqual([call("Udi")], dev.get_property.mock_calls)
        self.assertEqual([call("DUMMY_PATH", 2)], self.con_man.modem_manager.set_primary_sim_slot.mock_calls)
        self.assertEqual(
            [call("DUMMY_CON", "2", self.con_man.timeouts.connection_activation_timeout)],
            self.con_man._wait_gsm_sim_slot_to_change.mock_calls,
        )
        self.assertEqual("DUMMY_DEV", result)

    def test_deactivate_current_gsm_connection_01_current(self):
        active_cn = DummyNMActiveConnection()
        active_cn.get_connection_id = MagicMock(return_value="DUMMY_CON")
        self.con_man.timeouts.reset_connection_retry_timeout = MagicMock()
        self.con_man.network_manager.deactivate_connection = MagicMock()
        self.con_man._wait_connection_deactivation = MagicMock()
        self.con_man.current_connection = "DUMMY_CON"
        self.con_man.current_tier = "DUMMY_TIER"

        self.con_man.deactivate_current_gsm_connection(active_cn)

        self.assertEqual([call()], active_cn.get_connection_id.mock_calls)
        self.assertEqual([call("DUMMY_CON")], self.con_man.timeouts.reset_connection_retry_timeout.mock_calls)
        self.assertEqual([call(active_cn)], self.con_man.network_manager.deactivate_connection.mock_calls)
        self.assertEqual(
            [call(active_cn, self.con_man.timeouts.connection_activation_timeout)],
            self.con_man._wait_connection_deactivation.mock_calls,
        )
        self.assertEqual(None, self.con_man.current_connection)
        self.assertEqual(None, self.con_man.current_tier)

    def test_deactivate_current_gsm_connection_02_non_current(self):
        active_cn = DummyNMActiveConnection()
        active_cn.get_connection_id = MagicMock(return_value="DUMMY_OTHER_CON")
        self.con_man.timeouts.reset_connection_retry_timeout = MagicMock()
        self.con_man.network_manager.deactivate_connection = MagicMock()
        self.con_man._wait_connection_deactivation = MagicMock()
        self.con_man.current_connection = "DUMMY_CON"
        self.con_man.current_tier = "DUMMY_TIER"

        self.con_man.deactivate_current_gsm_connection(active_cn)

        self.assertEqual([call()], active_cn.get_connection_id.mock_calls)
        self.assertEqual(
            [call("DUMMY_OTHER_CON")], self.con_man.timeouts.reset_connection_retry_timeout.mock_calls
        )
        self.assertEqual([call(active_cn)], self.con_man.network_manager.deactivate_connection.mock_calls)
        self.assertEqual(
            [call(active_cn, self.con_man.timeouts.connection_activation_timeout)],
            self.con_man._wait_connection_deactivation.mock_calls,
        )
        self.assertEqual("DUMMY_CON", self.con_man.current_connection)
        self.assertEqual("DUMMY_TIER", self.con_man.current_tier)

    def test_wait_gsm_sim_slot_to_change_01_timeout(self):
        now = datetime.datetime(year=2000, month=1, day=2, hour=3, minute=4, second=5)
        timeout = datetime.timedelta(seconds=7)
        step = datetime.timedelta(seconds=1)
        dev = DummyNMDevice()
        dev.get_property = MagicMock(return_value="DUMMY_PATH")
        self.con_man.now = MagicMock(side_effect=[now, now + step, now + timeout + step])
        self.con_man.network_manager.find_device_for_connection = MagicMock(return_value=dev)
        self.con_man.modem_manager.get_primary_sim_slot = MagicMock(return_value="1")

        with patch.object(time, "sleep") as mock_sleep:
            result = self.con_man._wait_gsm_sim_slot_to_change("DUMMY_CON", "2", timeout)

        self.assertEqual([call(1)], mock_sleep.mock_calls)
        self.assertEqual([call("Udi")], dev.get_property.mock_calls)
        self.assertEqual([call(), call(), call()], self.con_man.now.mock_calls)
        self.assertEqual(
            [call("DUMMY_CON")], self.con_man.network_manager.find_device_for_connection.mock_calls
        )
        self.assertEqual([call("DUMMY_PATH")], self.con_man.modem_manager.get_primary_sim_slot.mock_calls)
        self.assertEqual(None, result)

    def test_wait_gsm_sim_slot_to_change_02_no_dev_then_exception_then_same_slot_then_success(self):
        now = datetime.datetime(year=2000, month=1, day=2, hour=3, minute=4, second=5)
        timeout = datetime.timedelta(seconds=7)
        step = datetime.timedelta(seconds=1)
        dev = DummyNMDevice()
        dev.get_property = MagicMock(side_effect=["OLD_PATH", "NEW_PATH"])
        self.con_man.now = MagicMock(
            side_effect=[
                now,
                now + step,
                now + step + step,
                now + step + step + step,
                now + step + step + step + step,
            ]
        )
        self.con_man.network_manager.find_device_for_connection = MagicMock(
            side_effect=[None, dbus.exceptions.DBusException(), dev, dev]
        )
        self.con_man.modem_manager.get_primary_sim_slot = MagicMock(side_effect=["1", "2"])

        with patch.object(time, "sleep") as mock_sleep:
            result = self.con_man._wait_gsm_sim_slot_to_change("DUMMY_CON", "2", timeout)

        self.assertEqual([call(1), call(1)], mock_sleep.mock_calls)
        self.assertEqual([call("Udi"), call("Udi")], dev.get_property.mock_calls)
        self.assertEqual([call(), call(), call(), call(), call()], self.con_man.now.mock_calls)
        self.assertEqual(
            [call("DUMMY_CON"), call("DUMMY_CON"), call("DUMMY_CON"), call("DUMMY_CON")],
            self.con_man.network_manager.find_device_for_connection.mock_calls,
        )
        self.assertEqual(
            [call("OLD_PATH"), call("NEW_PATH")], self.con_man.modem_manager.get_primary_sim_slot.mock_calls
        )
        self.assertEqual(dev, result)

    def test_wait_connection_activation_01_instant_success(self):
        now = datetime.datetime(year=2000, month=1, day=2, hour=3, minute=4, second=5)
        timeout = datetime.timedelta(seconds=7)
        step = datetime.timedelta(seconds=1)
        con = DummyNMConnection("dummy", {})
        con.get_property = MagicMock(return_value=connection_manager.NM_ACTIVE_CONNECTION_STATE_ACTIVATED)
        self.con_man.now = MagicMock(side_effect=[now, now + step])

        result = self.con_man._wait_connection_activation(con, timeout)

        self.assertEqual([call(), call()], self.con_man.now.mock_calls)
        self.assertEqual([call("State")], con.get_property.mock_calls)
        self.assertTrue(result)

    def test_wait_connection_activation_02_timeout(self):
        now = datetime.datetime(year=2000, month=1, day=2, hour=3, minute=4, second=5)
        timeout = datetime.timedelta(seconds=7)
        step = datetime.timedelta(seconds=1)
        con = DummyNMConnection("dummy", {})
        con.get_property = MagicMock(return_value=connection_manager.NM_ACTIVE_CONNECTION_STATE_DEACTIVATED)
        self.con_man.now = MagicMock(side_effect=[now, now + step, now + timeout + step])

        with patch.object(time, "sleep") as mock_sleep:
            result = self.con_man._wait_connection_activation(con, timeout)

        self.assertEqual([call(), call(), call()], self.con_man.now.mock_calls)
        self.assertEqual([call("State")], con.get_property.mock_calls)
        self.assertEqual([call(1)], mock_sleep.mock_calls)
        self.assertFalse(result)

    def test_wait_connection_deactivation_01_instant_success(self):
        now = datetime.datetime(year=2000, month=1, day=2, hour=3, minute=4, second=5)
        timeout = datetime.timedelta(seconds=7)
        step = datetime.timedelta(seconds=1)
        con = DummyNMConnection("dummy", {})
        con.get_property = MagicMock(return_value=connection_manager.NM_ACTIVE_CONNECTION_STATE_DEACTIVATED)
        self.con_man.now = MagicMock(side_effect=[now, now + step])

        self.con_man._wait_connection_deactivation(con, timeout)

        self.assertEqual([call(), call()], self.con_man.now.mock_calls)
        self.assertEqual([call("State")], con.get_property.mock_calls)

    def test_wait_connection_deactivation_02_unhandled_exception(self):
        now = datetime.datetime(year=2000, month=1, day=2, hour=3, minute=4, second=5)
        timeout = datetime.timedelta(seconds=7)
        step = datetime.timedelta(seconds=1)
        con = DummyNMConnection("dummy", {})
        exc = dbus.exceptions.DBusException()
        exc.get_dbus_name = MagicMock(return_value="org.freedesktop.DBus.Error.SomeRandomError")
        con.get_property = MagicMock(side_effect=exc)
        self.con_man.now = MagicMock(side_effect=[now, now + step, now + step + step, now + timeout + step])

        with patch.object(time, "sleep") as mock_sleep:
            self.con_man._wait_connection_deactivation(con, timeout)

        self.assertEqual([call(1), call(1)], mock_sleep.mock_calls)
        self.assertEqual([call(), call(), call(), call()], self.con_man.now.mock_calls)
        self.assertEqual([call("State"), call("State")], con.get_property.mock_calls)

    def test_wait_connection_deactivation_03_handled_exception(self):
        now = datetime.datetime(year=2000, month=1, day=2, hour=3, minute=4, second=5)
        timeout = datetime.timedelta(seconds=7)
        step = datetime.timedelta(seconds=1)
        con = DummyNMConnection("dummy", {})
        exc = dbus.exceptions.DBusException()
        exc.get_dbus_name = MagicMock(return_value="org.freedesktop.DBus.Error.UnknownMethod")
        con.get_property = MagicMock(side_effect=exc)
        self.con_man.now = MagicMock(side_effect=[now, now + step])

        with patch.object(time, "sleep") as mock_sleep:
            self.con_man._wait_connection_deactivation(con, timeout)

        self.assertEqual([], mock_sleep.mock_calls)
        self.assertEqual([call(), call()], self.con_man.now.mock_calls)
        self.assertEqual([call("State")], con.get_property.mock_calls)

    def test_set_device_metric_for_connection_01_no_devices(self):
        active_cn = DummyNMActiveConnection()
        active_cn.get_connection_id = MagicMock()
        dev = DummyNMDevice()
        active_cn.get_devices = MagicMock(return_value=[])
        dev.get_property = MagicMock()
        active_cn.get_connection_type = MagicMock()
        self.con_man.call_ifmetric = MagicMock()
        dev.set_metric = MagicMock()

        with patch.object(connection_manager, "connection_type_to_device_type") as mock_ct_to_dt:
            self.con_man.set_device_metric_for_connection(active_cn, 666)

        self.assertEqual([call()], active_cn.get_devices.mock_calls)
        self.assertEqual([], mock_ct_to_dt.mock_calls)
        self.assertEqual([], dev.get_property.mock_calls)
        self.assertEqual([], active_cn.get_connection_type.mock_calls)
        self.assertEqual([], self.con_man.call_ifmetric.mock_calls)
        self.assertEqual([], dev.set_metric.mock_calls)

    def test_set_device_metric_for_connection_02_modem(self):
        active_cn = DummyNMActiveConnection()
        active_cn.get_connection_id = MagicMock()
        dev = DummyNMDevice()
        active_cn.get_devices = MagicMock(return_value=[dev, "DUMMY_DEV"])
        active_cn.get_connection_type = MagicMock(return_value=666)
        dev.get_property = MagicMock(return_value="DUMMY_IF")
        self.con_man.call_ifmetric = MagicMock()
        dev.set_metric = MagicMock()

        with patch.object(connection_manager, "connection_type_to_device_type") as mock_ct_to_dt:
            mock_ct_to_dt.return_value = connection_manager.NM_DEVICE_TYPE_MODEM
            self.con_man.set_device_metric_for_connection(active_cn, 666)

        self.assertEqual([call()], active_cn.get_devices.mock_calls)
        self.assertEqual([call()], active_cn.get_connection_type.mock_calls)
        self.assertEqual([call(666)], mock_ct_to_dt.mock_calls)
        self.assertEqual([call("IpInterface")], dev.get_property.mock_calls)
        self.assertEqual([call("DUMMY_IF", 666)], self.con_man.call_ifmetric.mock_calls)
        self.assertEqual([], dev.set_metric.mock_calls)

    def test_set_device_metric_for_connection_03_not_modem(self):
        active_cn = DummyNMActiveConnection()
        active_cn.get_connection_id = MagicMock()
        dev = DummyNMDevice()
        active_cn.get_devices = MagicMock(return_value=[dev, "DUMMY_DEV"])
        active_cn.get_connection_type = MagicMock(return_value=666)
        dev.get_property = MagicMock(return_value="DUMMY_IF")
        self.con_man.call_ifmetric = MagicMock()
        dev.set_metric = MagicMock()

        with patch.object(connection_manager, "connection_type_to_device_type") as mock_ct_to_dt:
            mock_ct_to_dt.return_value = connection_manager.NM_DEVICE_TYPE_MODEM + 16
            self.con_man.set_device_metric_for_connection(active_cn, 666)

        self.assertEqual([call()], active_cn.get_devices.mock_calls)
        self.assertEqual([call()], active_cn.get_connection_type.mock_calls)
        self.assertEqual([call(666)], mock_ct_to_dt.mock_calls)
        self.assertEqual([], dev.get_property.mock_calls)
        self.assertEqual([], self.con_man.call_ifmetric.mock_calls)
        self.assertEqual([call(666)], dev.set_metric.mock_calls)

    def test_call_ifmetric(self):
        with patch.object(subprocess, "run") as mock_rum:
            self.con_man.call_ifmetric("IFACE", "METRIC")
        self.assertEqual(
            [call(["/usr/sbin/ifmetric", "IFACE", "METRIC"], shell=False, check=False)], mock_rum.mock_calls
        )

    def test_get_active_wifi_connections_01_empty(self):
        self.con_man.network_manager.get_active_connections = MagicMock(return_value={})

        with patch.object(connection_manager, "connection_type_to_device_type") as mock_ct_to_dt:
            result = self.con_man._get_active_wifi_connections()

        self.assertEqual([call()], self.con_man.network_manager.get_active_connections.mock_calls)
        self.assertEqual([], mock_ct_to_dt.mock_calls)
        self.assertEqual([], result)

    def test_get_active_wifi_connections_02_not_empty(self):
        active_cn1 = DummyNMActiveConnection()
        active_cn2 = DummyNMActiveConnection()
        active_cn3 = DummyNMActiveConnection()
        cn1 = DummyNMConnection("cn1", {})
        cn2 = DummyNMConnection("cn2", {})
        cn3 = DummyNMConnection("cn3", {})
        self.con_man.network_manager.get_active_connections = MagicMock(
            return_value={"dev1": active_cn1, "dev2": active_cn2, "dev3": active_cn3}
        )
        active_cn1.get_connection_type = MagicMock(return_value="CON1")
        active_cn2.get_connection_type = MagicMock(return_value="CON2")
        active_cn3.get_connection_type = MagicMock(return_value="CON3")
        active_cn1.get_connection_id = MagicMock(return_value="CN1")
        active_cn2.get_connection_id = MagicMock(return_value="CN2")
        active_cn3.get_connection_id = MagicMock(return_value="CN3")
        active_cn1.get_connection = MagicMock(return_value=cn1)
        active_cn2.get_connection = MagicMock(return_value=cn2)
        active_cn3.get_connection = MagicMock(return_value=cn3)
        cn1.get_settings = MagicMock(return_value={"802-11-wireless": {"mode": "ap"}})
        cn2.get_settings = MagicMock(return_value={"802-11-wireless": {"mode": "client"}})
        cn3.get_settings = MagicMock(return_value={})

        with patch.object(connection_manager, "connection_type_to_device_type") as mock_ct_to_dt:
            mock_ct_to_dt.side_effect = [NM_DEVICE_TYPE_WIFI, NM_DEVICE_TYPE_WIFI, NM_DEVICE_TYPE_MODEM]
            result = self.con_man._get_active_wifi_connections()

        self.assertEqual([call()], self.con_man.network_manager.get_active_connections.mock_calls)
        self.assertEqual([call()], active_cn1.get_connection_type.mock_calls)
        self.assertEqual([call()], active_cn2.get_connection_type.mock_calls)
        self.assertEqual([call()], active_cn3.get_connection_type.mock_calls)
        self.assertEqual([call("CON1"), call("CON2"), call("CON3")], mock_ct_to_dt.mock_calls)
        self.assertEqual([call()], active_cn1.get_connection.mock_calls)
        self.assertEqual([call()], active_cn2.get_connection.mock_calls)
        self.assertEqual([call()], active_cn3.get_connection.mock_calls)
        self.assertEqual([call()], cn1.get_settings.mock_calls)
        self.assertEqual([call()], cn2.get_settings.mock_calls)
        self.assertEqual([call()], cn3.get_settings.mock_calls)
        self.assertEqual([active_cn2], result)

    def test_cycle_loop(self):
        sample_tier = connection_manager.ConnectionTier("DUMMY_TIER", 666, ["wb-eth1"])
        self.con_man.check = MagicMock(side_effect=[(sample_tier, "wb-eth1"), (sample_tier, "wb-eth2")])
        self.con_man.set_current_connection = MagicMock()
        self.con_man.deactivate_lesser_gsm_connections = MagicMock()
        self.con_man.apply_metrics = MagicMock()
        self.con_man.current_tier = sample_tier
        self.con_man.current_connection = "wb-eth1"

        self.con_man.cycle_loop()

        self.assertEqual([call()], self.con_man.check.mock_calls)
        self.assertEqual([], self.con_man.set_current_connection.mock_calls)
        self.assertEqual([], self.con_man.deactivate_lesser_gsm_connections.mock_calls)
        self.assertEqual([], self.con_man.apply_metrics.mock_calls)

        self.con_man.cycle_loop()

        self.assertEqual([call(), call()], self.con_man.check.mock_calls)
        self.assertEqual([call("wb-eth2", sample_tier)], self.con_man.set_current_connection.mock_calls)
        self.assertEqual(
            [call("wb-eth2", sample_tier)], self.con_man.deactivate_lesser_gsm_connections.mock_calls
        )
        self.assertEqual([call()], self.con_man.apply_metrics.mock_calls)

    def test_ok_to_activate_connection_01_con_retry_is_active(self):
        self.con_man.timeouts.keep_sticky_connections_until = datetime.datetime(year=2000, month=1, day=2)
        self.con_man.timeouts.connection_retry_timeout_is_active = MagicMock(return_value=True)
        self.con_man.connection_is_sticky = MagicMock()
        self.con_man.timeouts.sticky_timeout_is_active = MagicMock()

        result = self.con_man.ok_to_activate_connection("wb-eth0")

        self.assertEqual(False, result)
        self.assertEqual(
            [call("wb-eth0")], self.con_man.timeouts.connection_retry_timeout_is_active.mock_calls
        )
        self.assertEqual([], self.con_man.connection_is_sticky.mock_calls)
        self.assertEqual([], self.con_man.timeouts.sticky_timeout_is_active.mock_calls)

    def test_ok_to_activate_connection_02_con_not_sticky_but_sticky_timeout_is_active(self):
        self.con_man.timeouts.keep_sticky_connections_until = datetime.datetime(year=2000, month=1, day=2)
        self.con_man.timeouts.connection_retry_timeout_is_active = MagicMock(return_value=False)
        self.con_man.connection_is_sticky = MagicMock(return_value=False)
        self.con_man.timeouts.sticky_timeout_is_active = MagicMock(return_value=True)

        result = self.con_man.ok_to_activate_connection("wb-eth0")

        self.assertEqual(True, result)
        self.assertEqual(
            [call("wb-eth0")], self.con_man.timeouts.connection_retry_timeout_is_active.mock_calls
        )
        self.assertEqual([call("wb-eth0")], self.con_man.connection_is_sticky.mock_calls)
        self.assertEqual([], self.con_man.timeouts.sticky_timeout_is_active.mock_calls)

    def test_ok_to_activate_connection_03_con_is_sticky_but_sticky_timeout_not_active(self):
        self.con_man.timeouts.keep_sticky_connections_until = datetime.datetime(year=2000, month=1, day=2)
        self.con_man.timeouts.connection_retry_timeout_is_active = MagicMock(return_value=False)
        self.con_man.connection_is_sticky = MagicMock(return_value=True)
        self.con_man.timeouts.sticky_timeout_is_active = MagicMock(return_value=False)

        result = self.con_man.ok_to_activate_connection("wb-eth0")

        self.assertEqual(True, result)
        self.assertEqual(
            [call("wb-eth0")], self.con_man.timeouts.connection_retry_timeout_is_active.mock_calls
        )
        self.assertEqual([call("wb-eth0")], self.con_man.connection_is_sticky.mock_calls)
        self.assertEqual([call()], self.con_man.timeouts.sticky_timeout_is_active.mock_calls)

    def test_ok_to_activate_connection_04_con_is_sticky_and_sticky_timeout_is_active(self):
        self.con_man.timeouts.keep_sticky_connections_until = datetime.datetime(year=2000, month=1, day=2)
        self.con_man.timeouts.connection_retry_timeout_is_active = MagicMock(return_value=False)
        self.con_man.connection_is_sticky = MagicMock(return_value=True)
        self.con_man.timeouts.sticky_timeout_is_active = MagicMock(return_value=True)

        result = self.con_man.ok_to_activate_connection("wb-eth0")

        self.assertEqual(False, result)
        self.assertEqual(
            [call("wb-eth0")], self.con_man.timeouts.connection_retry_timeout_is_active.mock_calls
        )
        self.assertEqual([call("wb-eth0")], self.con_man.connection_is_sticky.mock_calls)
        self.assertEqual([call()], self.con_man.timeouts.sticky_timeout_is_active.mock_calls)

    def test_ok_to_activate_connection_05_con_not_sticky_and_all_timeouts_false(self):
        self.con_man.timeouts.keep_sticky_connections_until = datetime.datetime(year=2000, month=1, day=2)
        self.con_man.timeouts.connection_retry_timeout_is_active = MagicMock(return_value=False)
        self.con_man.connection_is_sticky = MagicMock(return_value=False)
        self.con_man.timeouts.sticky_timeout_is_active = MagicMock(return_value=False)

        result = self.con_man.ok_to_activate_connection("wb-eth0")

        self.assertEqual(True, result)
        self.assertEqual(
            [call("wb-eth0")], self.con_man.timeouts.connection_retry_timeout_is_active.mock_calls
        )
        self.assertEqual([call("wb-eth0")], self.con_man.connection_is_sticky.mock_calls)
        self.assertEqual([], self.con_man.timeouts.sticky_timeout_is_active.mock_calls)

    def test_find_active_connection(self):
        self.con_man.network_manager.get_active_connections = MagicMock(return_value={"wb-eth0": "active"})

        result = self.con_man.find_active_connection("wb-eth0")

        self.assertEqual("active", result)
        self.assertEqual([call()], self.con_man.network_manager.get_active_connections.mock_calls)

    def test_find_activated_connection_01_no_active(self):
        dummy = DummyNMActiveConnection()
        self.con_man.find_active_connection = MagicMock(return_value=None)
        self.assertEqual(None, self.con_man.find_activated_connection("wb-eth0"))

    def test_find_activated_connection_02_active_but_not_activated(self):
        dummy = DummyNMActiveConnection()
        dummy.get_property = MagicMock(
            return_value=connection_manager.NM_ACTIVE_CONNECTION_STATE_ACTIVATED + 13
        )
        self.con_man.find_active_connection = MagicMock(return_value=dummy)
        self.assertEqual(None, self.con_man.find_activated_connection("wb-eth0"))
        self.assertEqual([call("State")], dummy.get_property.mock_calls)

    def test_find_activated_connection_03_active_and_activated(self):
        dummy = DummyNMActiveConnection()
        dummy.get_property = MagicMock(return_value=connection_manager.NM_ACTIVE_CONNECTION_STATE_ACTIVATED)
        self.con_man.find_active_connection = MagicMock(return_value=dummy)
        self.assertEqual(dummy, self.con_man.find_activated_connection("wb-eth0"))
        self.assertEqual([call("State")], dummy.get_property.mock_calls)

    def test_connection_is_gsm_01_no_connection(self):
        con = DummyNMConnection("wb-gsm0", {})
        self.con_man.network_manager.find_connection = MagicMock(return_value=None)
        con.get_connection_type = MagicMock()

        with patch.object(connection_manager, "connection_type_to_device_type") as mock_ct_to_dt:
            result = self.con_man.connection_is_gsm("wb-gsm0")

        self.assertEqual(False, result)
        self.assertEqual([], con.get_connection_type.mock_calls)
        self.assertEqual([], mock_ct_to_dt.mock_calls)

    def test_connection_is_gsm_02_not_gsm(self):
        con = DummyNMConnection("wb-gsm0", {})
        self.con_man.network_manager.find_connection = MagicMock(return_value=con)
        con.get_connection_type = MagicMock(return_value="dummy_type")

        with patch.object(connection_manager, "connection_type_to_device_type") as mock_ct_to_dt:
            mock_ct_to_dt.return_value = connection_manager.NM_DEVICE_TYPE_MODEM + 13
            result = self.con_man.connection_is_gsm("wb-gsm0")

        self.assertEqual(False, result)
        self.assertEqual([call()], con.get_connection_type.mock_calls)
        self.assertEqual([call("dummy_type")], mock_ct_to_dt.mock_calls)

    def test_connection_is_gsm_03_gsm(self):
        con = DummyNMConnection("wb-gsm0", {})
        self.con_man.network_manager.find_connection = MagicMock(return_value=con)
        con.get_connection_type = MagicMock(return_value="dummy_type")

        with patch.object(connection_manager, "connection_type_to_device_type") as mock_ct_to_dt:
            mock_ct_to_dt.return_value = connection_manager.NM_DEVICE_TYPE_MODEM
            result = self.con_man.connection_is_gsm("wb-gsm0")

        self.assertEqual(True, result)
        self.assertEqual([call()], con.get_connection_type.mock_calls)
        self.assertEqual([call("dummy_type")], mock_ct_to_dt.mock_calls)

    def test_connection_is_sticky_01_no_connection(self):
        con = DummyNMConnection("wb-gsm0", {})
        self.con_man.network_manager.find_connection = MagicMock(return_value=None)
        con.get_connection_type = MagicMock()

        with patch.object(connection_manager, "connection_type_to_device_type") as mock_ct_to_dt:
            result = self.con_man.connection_is_sticky("wb-gsm0")

        self.assertEqual(False, result)
        self.assertEqual([], con.get_connection_type.mock_calls)
        self.assertEqual([], mock_ct_to_dt.mock_calls)

    def test_connection_is_sticky_02_not_valid(self):
        con = DummyNMConnection("wb-gsm0", {})
        self.con_man.network_manager.find_connection = MagicMock(return_value=con)
        con.get_connection_type = MagicMock(return_value="dummy_type")

        with patch.object(connection_manager, "connection_type_to_device_type") as mock_ct_to_dt:
            mock_ct_to_dt.return_value = (
                connection_manager.NM_DEVICE_TYPE_MODEM + connection_manager.NM_DEVICE_TYPE_WIFI
            )
            result = self.con_man.connection_is_sticky("wb-gsm0")

        self.assertEqual(False, result)
        self.assertEqual([call()], con.get_connection_type.mock_calls)
        self.assertEqual([call("dummy_type")], mock_ct_to_dt.mock_calls)

    def test_connection_is_sticky_03_gsm(self):
        con = DummyNMConnection("wb-gsm0", {})
        self.con_man.network_manager.find_connection = MagicMock(return_value=con)
        con.get_connection_type = MagicMock(return_value="dummy_type")

        with patch.object(connection_manager, "connection_type_to_device_type") as mock_ct_to_dt:
            mock_ct_to_dt.return_value = connection_manager.NM_DEVICE_TYPE_MODEM
            result = self.con_man.connection_is_sticky("wb-gsm0")

        self.assertEqual(True, result)
        self.assertEqual([call()], con.get_connection_type.mock_calls)
        self.assertEqual([call("dummy_type")], mock_ct_to_dt.mock_calls)

    def test_connection_is_sticky_04_wifi(self):
        con = DummyNMConnection("wb-gsm0", {})
        self.con_man.network_manager.find_connection = MagicMock(return_value=con)
        con.get_connection_type = MagicMock(return_value="dummy_type")

        with patch.object(connection_manager, "connection_type_to_device_type") as mock_ct_to_dt:
            mock_ct_to_dt.return_value = connection_manager.NM_DEVICE_TYPE_WIFI
            result = self.con_man.connection_is_sticky("wb-gsm0")

        self.assertEqual(True, result)
        self.assertEqual([call()], con.get_connection_type.mock_calls)
        self.assertEqual([call("dummy_type")], mock_ct_to_dt.mock_calls)

    def test_set_current_connection_01_same(self):
        self.con_man.current_connection = "wb-eth0"
        self.con_man.current_tier = "first_tier"
        self.con_man.timeouts.touch_sticky_timeout = MagicMock()
        self.con_man.network_manager.find_connection = MagicMock(return_value="dummy_con")

        self.con_man.set_current_connection("wb-eth0", "dummy_tier")

        self.assertEqual("wb-eth0", self.con_man.current_connection)
        self.assertEqual("first_tier", self.con_man.current_tier)
        self.assertEqual([], self.con_man.timeouts.touch_sticky_timeout.mock_calls)
        self.assertEqual([], self.con_man.network_manager.find_connection.mock_calls)

    def test_set_current_connection_02_changed(self):
        self.con_man.current_connection = "wb-eth0"
        self.con_man.current_tier = "first_tier"
        self.con_man.timeouts.touch_sticky_timeout = MagicMock()
        self.con_man.network_manager.find_connection = MagicMock(return_value="dummy_con")

        self.con_man.set_current_connection("wb-eth1", "dummy_tier")

        self.assertEqual("wb-eth1", self.con_man.current_connection)
        self.assertEqual("dummy_tier", self.con_man.current_tier)
        self.assertEqual([call("dummy_con")], self.con_man.timeouts.touch_sticky_timeout.mock_calls)
        self.assertEqual([call("wb-eth1")], self.con_man.network_manager.find_connection.mock_calls)

    def test_deactivate_lesser_gsm_connections(self):
        con = DummyNMConnection("wb-gsm0", {})
        con.get_connection_id = MagicMock(return_value="wb-gsm0")
        con2 = DummyNMConnection("wb-gsm1", {})
        con2.get_connection_id = MagicMock(return_value="wb-gsm1")
        self.con_man.find_lesser_gsm_connections = MagicMock(return_value=[con, con2])
        self.con_man.deactivate_connection = MagicMock()

        self.con_man.deactivate_lesser_gsm_connections("wb-eth1", "dummy_tier")

        self.assertEqual([call("wb-eth1", "dummy_tier")], self.con_man.find_lesser_gsm_connections.mock_calls)
        self.assertEqual([call(con), call(con2)], self.con_man.deactivate_connection.mock_calls)

    def test_find_lesser_gsm_connections_01_current_is_gsm(self):
        self.con_man.config.tiers = [
            connection_manager.ConnectionTier("first_tier", 3, ["wb-eth0", "wb-gsm0"]),
            connection_manager.ConnectionTier("second_tier", 2, ["wb-eth1", "wb-gsm1"]),
            connection_manager.ConnectionTier("third_tier", 1, ["wb-eth2", "wb-gsm2"]),
        ]
        self.con_man.connection_is_gsm = MagicMock(side_effect=[False, False, True])
        self.con_man.find_active_connection = MagicMock(return_value="dummy_con")

        result = list(self.con_man.find_lesser_gsm_connections("wb-gsm1", self.con_man.config.tiers[1]))

        self.assertEqual(
            [call("wb-eth1"), call("wb-eth2"), call("wb-gsm2")], self.con_man.connection_is_gsm.mock_calls
        )
        self.assertEqual(["dummy_con"], result)

    def test_find_lesser_gsm_connections_02_current_not_gsm(self):
        self.con_man.config.tiers = [
            connection_manager.ConnectionTier("first_tier", 3, ["wb-eth0", "wb-gsm0"]),
            connection_manager.ConnectionTier("second_tier", 2, ["wb-eth1", "wb-gsm1"]),
            connection_manager.ConnectionTier("third_tier", 1, ["wb-eth2", "wb-gsm2"]),
        ]
        self.con_man.connection_is_gsm = MagicMock(side_effect=[True, False, True])
        self.con_man.find_active_connection = MagicMock(side_effect=["dummy_con1", "dummy_con2"])

        result = list(self.con_man.find_lesser_gsm_connections("wb-eth1", self.con_man.config.tiers[1]))

        self.assertEqual(
            [call("wb-gsm1"), call("wb-eth2"), call("wb-gsm2")], self.con_man.connection_is_gsm.mock_calls
        )
        self.assertEqual(["dummy_con1", "dummy_con2"], result)

    def test_apply_metrics(self):
        tier = connection_manager.ConnectionTier(
            "dummy_tier", 1, ["wb-eth0", "wb-eth1", "wb-gsm0", "wb-wifi"]
        )
        tier.get_base_route_metric = MagicMock(return_value=100)
        self.con_man.config.tiers = [tier]
        self.con_man.current_connection = "wb-wifi"
        self.con_man.network_manager.get_active_connections = MagicMock(
            return_value={"wb-eth0": "dummy_con1", "wb-gsm0": "dummy_con2", "wb-wifi": "dummy_con3"}
        )
        self.con_man.set_device_metric_for_connection = MagicMock()

        self.con_man.apply_metrics()

        self.assertEqual([call()], self.con_man.network_manager.get_active_connections.mock_calls)
        self.assertEqual([call(), call()], tier.get_base_route_metric.mock_calls)
        self.assertEqual(
            [call("dummy_con1", 100), call("dummy_con2", 101), call("dummy_con3", 55)],
            self.con_man.set_device_metric_for_connection.mock_calls,
        )


class MainTests(TestCase):
    def setUp(self) -> None:
        importlib.reload(connection_manager)

        connection_manager.NetworkManager = DummyNetworkManager
        connection_manager.NetworkAwareConfigFile = DummyConfigFile
        connection_manager.ModemManager = DummyModemManager

        self.dummy_json = DummyBytesIO()

    def tearDown(self) -> None:
        importlib.reload(connection_manager)

    def test_json_loading_errors_01_file_not_found(self):
        connection_manager.read_config_json = MagicMock(side_effect=FileNotFoundError())
        connection_manager.init_logging = MagicMock()

        result = connection_manager.main()

        self.assertEqual([call()], connection_manager.read_config_json.mock_calls)
        self.assertEqual([], connection_manager.init_logging.mock_calls)
        self.assertEqual(6, result)

    def test_json_loading_errors_02_permission_error(self):
        connection_manager.read_config_json = MagicMock(side_effect=PermissionError())
        connection_manager.init_logging = MagicMock()

        result = connection_manager.main()

        self.assertEqual([call()], connection_manager.read_config_json.mock_calls)
        self.assertEqual([], connection_manager.init_logging.mock_calls)
        self.assertEqual(6, result)

    def test_json_loading_errors_03_os_error(self):
        connection_manager.read_config_json = MagicMock(side_effect=OSError())
        connection_manager.init_logging = MagicMock()

        result = connection_manager.main()

        self.assertEqual([call()], connection_manager.read_config_json.mock_calls)
        self.assertEqual([], connection_manager.init_logging.mock_calls)
        self.assertEqual(6, result)

    def test_json_loading_errors_04_json_decode_error(self):
        connection_manager.read_config_json = MagicMock(
            side_effect=json.decoder.JSONDecodeError("msg", "doc", 1)
        )
        connection_manager.init_logging = MagicMock()

        result = connection_manager.main()

        self.assertEqual([call()], connection_manager.read_config_json.mock_calls)
        self.assertEqual([], connection_manager.init_logging.mock_calls)
        self.assertEqual(6, result)

    def test_json_loading_errors_05_random_exception(self):
        connection_manager.read_config_json = MagicMock(side_effect=IndentationError())
        connection_manager.init_logging = MagicMock()

        with self.assertRaises(IndentationError):
            connection_manager.main()

        self.assertEqual([call()], connection_manager.read_config_json.mock_calls)
        self.assertEqual([], connection_manager.init_logging.mock_calls)

    def test_config_errors_01_improperly_configured(self):
        connection_manager.read_config_json = MagicMock(return_value=self.dummy_json)
        self.dummy_json.get = MagicMock(return_value="DUMMY_DEBUG")
        connection_manager.init_logging = MagicMock()
        DummyConfigFile.load_config = MagicMock(side_effect=connection_manager.ImproperlyConfigured())

        with patch.object(DummyConfigFile, "__init__") as mock_config_init:
            mock_config_init.return_value = None
            result = connection_manager.main()

        self.assertEqual([call()], connection_manager.read_config_json.mock_calls)
        self.assertEqual([call("debug", False)], self.dummy_json.get.mock_calls)
        self.assertEqual(1, len(mock_config_init.mock_calls))
        self.assertEqual(0, len(mock_config_init.mock_calls[0].args))
        self.assertEqual(1, len(mock_config_init.mock_calls[0].kwargs))
        self.assertEqual("network_manager", list(mock_config_init.mock_calls[0].kwargs.keys())[0])
        nm_val = list(mock_config_init.mock_calls[0].kwargs.values())[0]
        self.assertTrue(isinstance(nm_val, DummyNetworkManager))
        self.assertEqual([call(cfg=self.dummy_json)], DummyConfigFile.load_config.mock_calls)
        self.assertEqual([call("DUMMY_DEBUG")], connection_manager.init_logging.mock_calls)
        self.assertEqual(6, result)

    def test_config_errors_02_random_exception(self):
        connection_manager.read_config_json = MagicMock(return_value=self.dummy_json)
        self.dummy_json.get = MagicMock(return_value="DUMMY_DEBUG")
        connection_manager.init_logging = MagicMock()
        DummyConfigFile.load_config = MagicMock(side_effect=IndentationError())

        with patch.object(DummyConfigFile, "__init__") as mock_config_init:
            mock_config_init.return_value = None
            with self.assertRaises(IndentationError):
                connection_manager.main()

        self.assertEqual([call()], connection_manager.read_config_json.mock_calls)
        self.assertEqual([call("debug", False)], self.dummy_json.get.mock_calls)
        self.assertEqual(1, len(mock_config_init.mock_calls))
        self.assertEqual(0, len(mock_config_init.mock_calls[0].args))
        self.assertEqual(1, len(mock_config_init.mock_calls[0].kwargs))
        self.assertEqual("network_manager", list(mock_config_init.mock_calls[0].kwargs.keys())[0])
        nm_val = list(mock_config_init.mock_calls[0].kwargs.values())[0]
        self.assertTrue(isinstance(nm_val, DummyNetworkManager))
        self.assertEqual([call(cfg=self.dummy_json)], DummyConfigFile.load_config.mock_calls)
        self.assertEqual([call("DUMMY_DEBUG")], connection_manager.init_logging.mock_calls)

    def test_later_main_stage_no_connections(self):
        connection_manager.read_config_json = MagicMock(return_value=self.dummy_json)
        self.dummy_json.get = MagicMock(return_value="DUMMY_DEBUG")
        connection_manager.init_logging = MagicMock()
        DummyConfigFile.load_config = MagicMock()
        DummyConfigFile.has_connections = MagicMock(return_value=False)

        with patch.object(signal, "signal") as mock_signal, patch.object(
            DummyConfigFile, "__init__"
        ) as mock_config_init:
            mock_config_init.return_value = None
            result = connection_manager.main()

        self.assertEqual([call()], connection_manager.read_config_json.mock_calls)
        self.assertEqual([call("DUMMY_DEBUG")], connection_manager.init_logging.mock_calls)
        self.assertEqual([call("debug", False)], self.dummy_json.get.mock_calls)
        self.assertEqual(
            [call(cfg=self.dummy_json)], connection_manager.NetworkAwareConfigFile.load_config.mock_calls
        )
        self.assertEqual([call(signal.SIGINT, signal.SIG_DFL)], mock_signal.mock_calls)
        self.assertEqual([call()], DummyConfigFile.has_connections.mock_calls)
        self.assertEqual(0, result)

    def test_later_main_stage_unhandled_mm_fail(self):
        connection_manager.read_config_json = MagicMock(return_value=self.dummy_json)
        self.dummy_json.get = MagicMock(return_value="DUMMY_DEBUG")
        connection_manager.init_logging = MagicMock()
        DummyConfigFile.load_config = MagicMock()
        DummyConfigFile.has_connections = MagicMock(return_value=True)
        connection_manager.ConnectionManager.cycle_loop = MagicMock()

        with patch.object(signal, "signal") as mock_signal, patch.object(
            DummyConfigFile, "__init__"
        ) as mock_config_init, patch.object(DummyModemManager, "__init__") as mock_mm_init, patch.object(
            connection_manager.ConnectionManager, "__init__"
        ) as mock_cm_init, patch.object(
            time, "sleep"
        ) as mock_sleep:
            mock_cm_init.return_value = None
            mock_mm_init.side_effect = RuntimeError()
            mock_config_init.return_value = None
            mock_sleep.side_effect = [1, 2, 3]
            with self.assertRaises(RuntimeError):
                connection_manager.main()

        self.assertEqual([call()], connection_manager.read_config_json.mock_calls)
        self.assertEqual([call("DUMMY_DEBUG")], connection_manager.init_logging.mock_calls)
        self.assertEqual([call("debug", False)], self.dummy_json.get.mock_calls)
        self.assertEqual([call(cfg=self.dummy_json)], DummyConfigFile.load_config.mock_calls)
        self.assertEqual([call(signal.SIGINT, signal.SIG_DFL)], mock_signal.mock_calls)
        self.assertEqual([call()], DummyConfigFile.has_connections.mock_calls)
        self.assertEqual([call()], mock_mm_init.mock_calls)
        self.assertEqual(0, mock_cm_init.call_count)
        self.assertEqual([], connection_manager.ConnectionManager.cycle_loop.mock_calls)
        self.assertEqual([], mock_sleep.mock_calls)

    def test_later_main_stage_handled_mm_fail(self):
        connection_manager.read_config_json = MagicMock(return_value=self.dummy_json)
        self.dummy_json.get = MagicMock(return_value="DUMMY_DEBUG")
        connection_manager.init_logging = MagicMock()
        DummyConfigFile.load_config = MagicMock()
        DummyConfigFile.has_connections = MagicMock(return_value=True)
        connection_manager.ConnectionManager.cycle_loop = MagicMock()

        with patch.object(signal, "signal") as mock_signal, patch.object(
            DummyConfigFile, "__init__"
        ) as mock_config_init, patch.object(DummyModemManager, "__init__") as mock_mm_init, patch.object(
            connection_manager.ConnectionManager, "__init__"
        ) as mock_cm_init, patch.object(
            time, "sleep"
        ) as mock_sleep:
            mock_cm_init.return_value = None
            mock_mm_init.side_effect = dbus.exceptions.DBusException()
            mock_config_init.return_value = None
            mock_sleep.side_effect = [1, 2, 3]
            with self.assertRaises(StopIteration):
                connection_manager.main()

        self.assertEqual([call()], connection_manager.read_config_json.mock_calls)
        self.assertEqual([call("DUMMY_DEBUG")], connection_manager.init_logging.mock_calls)
        self.assertEqual([call("debug", False)], self.dummy_json.get.mock_calls)
        self.assertEqual([call(cfg=self.dummy_json)], DummyConfigFile.load_config.mock_calls)
        self.assertEqual([call(signal.SIGINT, signal.SIG_DFL)], mock_signal.mock_calls)
        self.assertEqual([call()], DummyConfigFile.has_connections.mock_calls)
        self.assertEqual([call()], mock_mm_init.mock_calls)
        self.assertEqual(1, mock_cm_init.call_count)
        self.assertEqual(3, len(mock_cm_init.mock_calls[0].kwargs))
        self.assertTrue(
            isinstance(mock_cm_init.mock_calls[0].kwargs.get("network_manager"), DummyNetworkManager)
        )
        self.assertTrue(isinstance(mock_cm_init.mock_calls[0].kwargs.get("config"), DummyConfigFile))
        self.assertEqual(None, mock_cm_init.mock_calls[0].kwargs.get("modem_manager"))
        self.assertEqual(
            [call(), call(), call(), call()], connection_manager.ConnectionManager.cycle_loop.mock_calls
        )
        self.assertEqual(
            [
                call(connection_manager.CHECK_PERIOD.total_seconds()),
                call(connection_manager.CHECK_PERIOD.total_seconds()),
                call(connection_manager.CHECK_PERIOD.total_seconds()),
                call(connection_manager.CHECK_PERIOD.total_seconds()),
            ],
            mock_sleep.mock_calls,
        )

    def test_later_main_stage_success(self):
        connection_manager.read_config_json = MagicMock(return_value=self.dummy_json)
        self.dummy_json.get = MagicMock(return_value="DUMMY_DEBUG")
        connection_manager.init_logging = MagicMock()
        DummyConfigFile.load_config = MagicMock()
        DummyConfigFile.has_connections = MagicMock(return_value=True)
        connection_manager.ConnectionManager.cycle_loop = MagicMock()

        with patch.object(signal, "signal") as mock_signal, patch.object(
            DummyConfigFile, "__init__"
        ) as mock_config_init, patch.object(DummyModemManager, "__init__") as mock_mm_init, patch.object(
            connection_manager.ConnectionManager, "__init__"
        ) as mock_cm_init, patch.object(
            time, "sleep"
        ) as mock_sleep:
            mock_cm_init.return_value = None
            mock_mm_init.return_value = None
            mock_config_init.return_value = None
            mock_sleep.side_effect = [1, 2, 3]
            with self.assertRaises(StopIteration):
                connection_manager.main()

        self.assertEqual([call()], connection_manager.read_config_json.mock_calls)
        self.assertEqual([call("DUMMY_DEBUG")], connection_manager.init_logging.mock_calls)
        self.assertEqual([call("debug", False)], self.dummy_json.get.mock_calls)
        self.assertEqual(
            [call(cfg=self.dummy_json)], connection_manager.NetworkAwareConfigFile.load_config.mock_calls
        )
        self.assertEqual([call(signal.SIGINT, signal.SIG_DFL)], mock_signal.mock_calls)
        self.assertEqual([call()], DummyConfigFile.has_connections.mock_calls)
        self.assertEqual([call()], mock_mm_init.mock_calls)
        self.assertEqual(1, mock_cm_init.call_count)
        self.assertEqual(3, len(mock_cm_init.mock_calls[0].kwargs))
        self.assertTrue(
            isinstance(mock_cm_init.mock_calls[0].kwargs.get("network_manager"), DummyNetworkManager)
        )
        self.assertTrue(isinstance(mock_cm_init.mock_calls[0].kwargs.get("config"), DummyConfigFile))
        self.assertTrue(isinstance(mock_cm_init.mock_calls[0].kwargs.get("modem_manager"), DummyModemManager))
        self.assertEqual(
            [call(), call(), call(), call()], connection_manager.ConnectionManager.cycle_loop.mock_calls
        )
        self.assertEqual(
            [
                call(connection_manager.CHECK_PERIOD.total_seconds()),
                call(connection_manager.CHECK_PERIOD.total_seconds()),
                call(connection_manager.CHECK_PERIOD.total_seconds()),
                call(connection_manager.CHECK_PERIOD.total_seconds()),
            ],
            mock_sleep.mock_calls,
        )
