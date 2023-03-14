import datetime
from unittest.mock import MagicMock

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


def test_connection_tier():
    tier = ConnectionTier("high", 3, ["wb-eth0", "wb-eth1"])
    assert tier.name == "high"
    assert tier.priority == 3
    assert len(tier.connections) == 2
    assert "wb-eth0" in tier.connections
    assert "wb-eth1" in tier.connections


def test_connection_tier_route_metrics():
    tier = ConnectionTier("high", 3, ["wb-eth0", "wb-eth1"])
    assert tier.get_base_route_metric() == 105
    tier = ConnectionTier("medium", 2, ["wb-eth0", "wb-eth1"])
    assert tier.get_base_route_metric() == 205
    tier = ConnectionTier("low", 1, ["wb-eth0", "wb-eth1"])
    assert tier.get_base_route_metric() == 305


def test_config_file_empty():
    net_man = FakeNetworkManager()
    net_man.fake_add_gsm("wb-debug", never_default=True, autoconnect=True)
    net_man.fake_add_gsm("wb-gsm-sim1")
    net_man.fake_add_gsm("wb-gsm-sim2", autoconnect=True)
    net_man.fake_add_wifi_client("wb-wifi-client")
    net_man.fake_add_wifi_client("wb-wifi-client2", autoconnect=True)
    net_man.fake_add_ethernet("wb-eth0")
    net_man.fake_add_ethernet("wb-eth1", autoconnect="true")
    cfg = {}
    ConnectionManagerConfigFile.get_network_manager = MagicMock(return_value=net_man)
    conffile = ConnectionManagerConfigFile(cfg)
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


def test_config_file_normal():
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
    conffile = ConnectionManagerConfigFile(cfg)
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


def test_config_file_bad_cc_url():
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
        ConnectionManagerConfigFile(cfg)


def test_config_file_bad_cc_payload():
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
        ConnectionManagerConfigFile(cfg)
