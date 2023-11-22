import json
from pathlib import Path

import pytest

from wb.nm_helper.network_interfaces_adapter import NetworkInterfacesAdapter


@pytest.mark.parametrize(
    "file_name,connections",
    [
        (
            # no new line at the end of file
            "tests/data/interfaces_no_newline",
            [
                {
                    "allow-hotplug": True,
                    "auto": False,
                    "method": "static",
                    "mode": "can",
                    "name": "can0",
                    "options": {"bitrate": 125000},
                    "type": "can",
                }
            ],
        ),
        (
            # hwaddress
            "tests/data/interfaces_hwaddress",
            [
                {
                    "allow-hotplug": True,
                    "auto": False,
                    "method": "dhcp",
                    "mode": "inet",
                    "name": "eth0",
                    "options": {"hwaddress": "94:C6:91:91:4D:5A"},
                    "type": "dhcp",
                },
                {
                    "allow-hotplug": True,
                    "auto": False,
                    "method": "dhcp",
                    "mode": "inet",
                    "name": "eth1",
                    "options": {"hwaddress": "94:C6:91:91:4D:6A"},
                    "type": "dhcp",
                },
            ],
        ),
    ],
)
def test_parsing(file_name, connections):
    adapter = NetworkInterfacesAdapter(file_name)
    assert adapter.get_connections() == connections


def test_apply_no_changes():
    with open("tests/data/ui.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    adapter = NetworkInterfacesAdapter("tests/data/interfaces")

    res = adapter.apply(cfg["ui"]["connections"], True)
    assert len(res.unmanaged_connections) == 5
    assert res.managed_interfaces == ["can0", "eth0", "eth1", "eth2", "wlan0"]
    assert not res.released_interfaces
    assert res.is_changed is False


def test_apply_changes():
    with open("tests/data/ui.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    generated = Path("tests/data/interfaces_generated").read_text(encoding="utf-8")

    adapter = NetworkInterfacesAdapter("tests/data/interfaces")

    cfg["ui"]["connections"][9]["auto"] = True
    res = adapter.apply(cfg["ui"]["connections"], True)
    assert len(res.unmanaged_connections) == 5
    assert res.managed_interfaces == ["can0", "eth0", "eth1", "eth2", "wlan0"]
    assert not res.released_interfaces
    assert res.is_changed is True
    assert adapter.format() == generated


def test_apply_remove_iface():
    with open("tests/data/ui.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    adapter = NetworkInterfacesAdapter("tests/data/interfaces")

    del cfg["ui"]["connections"][9]
    res = adapter.apply(cfg["ui"]["connections"], True)
    assert len(res.unmanaged_connections) == 5
    assert res.managed_interfaces == ["can0", "eth0", "eth1", "eth2"]
    assert res.released_interfaces == ["wlan0"]
    assert res.is_changed is False
