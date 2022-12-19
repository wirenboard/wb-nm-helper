import dbus
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
                    "options": {"bitrate": "125000"},
                    "type": "can",
                }
            ],
        )
    ],
)
def test_parsing(file_name, connections):
    adapter = NetworkInterfacesAdapter(file_name)
    assert adapter.get_connections() == connections
