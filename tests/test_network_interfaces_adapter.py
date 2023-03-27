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
        )
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
    assert res.managed_interfaces == ["can0", "eth0", "eth1", "wlan0"]
    assert not res.released_interfaces
    assert res.is_changed is False


def test_apply_changes():
    with open("tests/data/ui.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    generated = Path("tests/data/interfaces_generated").read_text(encoding="utf-8")

    adapter = NetworkInterfacesAdapter("tests/data/interfaces")

    cfg["ui"]["connections"][8]["auto"] = True
    res = adapter.apply(cfg["ui"]["connections"], True)
    assert len(res.unmanaged_connections) == 5
    assert res.managed_interfaces == ["can0", "eth0", "eth1", "wlan0"]
    assert not res.released_interfaces
    assert res.is_changed is True
    assert adapter.format().replace("\n", "") == generated.replace("\n", "")


def test_apply_remove_iface():
    with open("tests/data/ui.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)

    adapter = NetworkInterfacesAdapter("tests/data/interfaces")

    del cfg["ui"]["connections"][8]
    res = adapter.apply(cfg["ui"]["connections"], True)
    assert len(res.unmanaged_connections) == 5
    assert res.managed_interfaces == ["can0", "eth0", "eth1"]
    assert res.released_interfaces == ["wlan0"]
    assert res.is_changed is False
