import datetime

import pytest

from wb.nm_helper.connection_manager import (
    DEFAULT_CONNECTIVITY_CHECK_PAYLOAD,
    DEFAULT_CONNECTIVITY_CHECK_URL,
    DEFAULT_STICKY_SIM_PERIOD,
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
    assert tier.get_route_metric() == 105
    tier = ConnectionTier("medium", 2, ["wb-eth0", "wb-eth1"])
    assert tier.get_route_metric() == 205
    tier = ConnectionTier("low", 1, ["wb-eth0", "wb-eth1"])
    assert tier.get_route_metric() == 305


def test_config_file_empty():
    cfg = {}
    cf = ConnectionManagerConfigFile(cfg)
    assert cf.debug is False
    assert cf.connectivity_check_url == DEFAULT_CONNECTIVITY_CHECK_URL
    assert cf.connectivity_check_payload == DEFAULT_CONNECTIVITY_CHECK_PAYLOAD
    assert cf.sticky_sim_period == DEFAULT_STICKY_SIM_PERIOD
    assert len(cf.tiers) == 3
    assert cf.tiers[0].name == "high"
    assert cf.tiers[0].priority == 3
    assert cf.tiers[0].connections == []
    assert cf.tiers[1].name == "medium"
    assert cf.tiers[1].priority == 2
    assert cf.tiers[1].connections == []
    assert cf.tiers[2].name == "low"
    assert cf.tiers[2].priority == 1
    assert cf.tiers[2].connections == []


def test_config_file_normal():
    cfg = {
        "debug": True,
        "sticky_sim_period_s": 451,
        "connectivity_check_url": "http://test-server/test-url",
        "connectivity_check_payload": "Dummy CC Payload",
        "tiers": {
            "medium": ["wb-wifi-client"],
            "high": ["wb-eth0", "wb-eth1"],
            "low": ["wb-gsm-sim1", "wb-gsm-sim2"],
        },
    }
    cf = ConnectionManagerConfigFile(cfg)
    assert cf.debug is True
    assert cf.connectivity_check_url == "http://test-server/test-url"
    assert cf.connectivity_check_payload == "Dummy CC Payload"
    assert cf.sticky_sim_period == datetime.timedelta(seconds=451)
    assert len(cf.tiers) == 3
    assert cf.tiers[0].name == "high"
    assert cf.tiers[0].priority == 3
    assert cf.tiers[0].connections == ["wb-eth0", "wb-eth1"]
    assert cf.tiers[1].name == "medium"
    assert cf.tiers[1].priority == 2
    assert cf.tiers[1].connections == ["wb-wifi-client"]
    assert cf.tiers[2].name == "low"
    assert cf.tiers[2].priority == 1
    assert cf.tiers[2].connections == ["wb-gsm-sim1", "wb-gsm-sim2"]


def test_config_file_bad_cc_url():
    cfg = {
        "debug": True,
        "sticky_sim_period_s": 451,
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
        "sticky_sim_period_s": 451,
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
