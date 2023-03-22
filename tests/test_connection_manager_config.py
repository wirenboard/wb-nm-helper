import datetime
import unittest

import pytest

from tests.mm_mock import FakeNetworkManager
from wb.nm_helper.connection_manager import (
    DEFAULT_CONNECTIVITY_CHECK_PAYLOAD,
    DEFAULT_CONNECTIVITY_CHECK_URL,
    DEFAULT_STICKY_CONNECTION_PERIOD,
    ConnectionManagerConfigFile,
    ConnectionTier,
    ImproperlyConfigured,
)


class ConManConfigFileTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.net_man = FakeNetworkManager()

    def test_01_connection_tier(self):
        tier = ConnectionTier("high", 3, ["wb-eth0", "wb-eth1"])
        assert tier.name == "high"
        assert tier.priority == 3
        assert len(tier.connections) == 2
        assert "wb-eth0" in tier.connections
        assert "wb-eth1" in tier.connections

    def test_02_connection_tier_route_metrics(self):
        tier = ConnectionTier("high", 3, ["wb-eth0", "wb-eth1"])
        assert tier.get_base_route_metric() == 105
        tier = ConnectionTier("medium", 2, ["wb-eth0", "wb-eth1"])
        assert tier.get_base_route_metric() == 205
        tier = ConnectionTier("low", 1, ["wb-eth0", "wb-eth1"])
        assert tier.get_base_route_metric() == 305

    def test_config_file_empty(self):
        self.net_man.fake_add_gsm("wb-debug", never_default=True, autoconnect=True, device_connected=True)
        self.net_man.fake_add_gsm("wb-gsm-sim1", device_connected=True)
        self.net_man.fake_add_gsm("wb-gsm-sim2", autoconnect=True, device_connected=True)
        self.net_man.fake_add_wifi_client("wb-wifi-client", device_connected=True)
        self.net_man.fake_add_wifi_client("wb-wifi-client2", autoconnect=True, device_connected=True)
        self.net_man.fake_add_ethernet("wb-eth0", device_connected=True)
        self.net_man.fake_add_ethernet("wb-eth1", autoconnect="true", device_connected=True)
        cfg = {}
        conffile = ConnectionManagerConfigFile(network_manager=self.net_man)
        conffile.load_config(cfg=cfg)
        assert conffile.debug is False
        assert conffile.connectivity_check_url == DEFAULT_CONNECTIVITY_CHECK_URL
        assert conffile.connectivity_check_payload == DEFAULT_CONNECTIVITY_CHECK_PAYLOAD
        assert conffile.sticky_connection_period == DEFAULT_STICKY_CONNECTION_PERIOD
        assert len(conffile.tiers) == 3
        assert conffile.tiers[0].name == "high"
        assert conffile.tiers[0].priority == 3
        assert conffile.tiers[0].connections == ["wb-eth1"]
        assert conffile.tiers[1].name == "medium"
        assert conffile.tiers[1].priority == 2
        assert conffile.tiers[1].connections == ["wb-wifi-client2"]
        assert conffile.tiers[2].name == "low"
        assert conffile.tiers[2].priority == 1
        assert conffile.tiers[2].connections == ["wb-gsm-sim2"]

    def test_config_file_normal(self):
        self.net_man.fake_add_gsm("wb-gsm-sim1", device_connected=True)
        self.net_man.fake_add_gsm("wb-gsm-sim2", autoconnect=True, device_connected=True)
        self.net_man.fake_add_wifi_client("wb-wifi-client", device_connected=True)
        self.net_man.fake_add_ethernet("wb-eth0", device_connected=True)
        self.net_man.fake_add_ethernet("wb-eth1", autoconnect="true", device_connected=True)
        cfg = {
            "debug": True,
            "sticky_connection_period_s": 451,
            "connectivity_check_url": "http://test-server/test-url",
            "connectivity_check_payload": "Dummy CC Payload",
            "tiers": {
                "medium": ["wb-wifi-client"],
                "high": ["wb-eth0", "wb-eth1"],
                "low": ["wb-gsm-sim1", "wb-gsm-sim2"],
            },
        }
        conffile = ConnectionManagerConfigFile(network_manager=self.net_man)
        conffile.load_config(cfg=cfg)
        assert conffile.debug is True
        assert conffile.connectivity_check_url == "http://test-server/test-url"
        assert conffile.connectivity_check_payload == "Dummy CC Payload"
        assert conffile.sticky_connection_period == datetime.timedelta(seconds=451)
        assert len(conffile.tiers) == 3
        assert conffile.tiers[0].name == "high"
        assert conffile.tiers[0].priority == 3
        assert conffile.tiers[0].connections == ["wb-eth0", "wb-eth1"]
        assert conffile.tiers[1].name == "medium"
        assert conffile.tiers[1].priority == 2
        assert conffile.tiers[1].connections == ["wb-wifi-client"]
        assert conffile.tiers[2].name == "low"
        assert conffile.tiers[2].priority == 1
        assert conffile.tiers[2].connections == ["wb-gsm-sim1", "wb-gsm-sim2"]

    def test_config_file_bad_cc_url(self):
        cfg = {
            "debug": True,
            "sticky_connection_period_s": 451,
            "connectivity_check_url": "zzz",
            "connectivity_check_payload": "Dummy CC Payload",
            "tiers": {
                "medium": ["wb-wifi-client"],
                "high": ["wb-eth0", "wb-eth1"],
                "low": ["wb-gsm-sim1", "wb-gsm-sim2"],
            },
        }
        with pytest.raises(ImproperlyConfigured):
            obj = ConnectionManagerConfigFile(network_manager=self.net_man)
            obj.load_config(cfg=cfg)

    def test_config_file_bad_cc_payload(self):
        cfg = {
            "debug": True,
            "sticky_connection_period_s": 451,
            "connectivity_check_url": "http://test-server/test-url",
            "connectivity_check_payload": "",
            "tiers": {
                "medium": ["wb-wifi-client"],
                "high": ["wb-eth0", "wb-eth1"],
                "low": ["wb-gsm-sim1", "wb-gsm-sim2"],
            },
        }
        with pytest.raises(ImproperlyConfigured):
            obj = ConnectionManagerConfigFile(network_manager=self.net_man)
            obj.load_config(cfg=cfg)

    def test_config_file_is_connection_managed(self):
        self.net_man.fake_add_ethernet("wb-eth0", device_connected=True)
        self.net_man.fake_add_ethernet("wb-eth1", device_connected=True, managed=False)
        conffile = ConnectionManagerConfigFile(network_manager=self.net_man)
        eth0 = self.net_man.find_connection("wb-eth0")
        eth1 = self.net_man.find_connection("wb-eth1")
        assert conffile.is_connection_unmanaged(eth0) is False
        assert conffile.is_connection_unmanaged(eth1) is True
