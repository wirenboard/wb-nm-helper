import json
import sys

from .network_interfaces_adapter import NetworkInterfacesAdapter
from .network_manager_adapter import NetworkManagerAdapter

JSON_INDENT_LEVEL = 2
SERIAL_FILE = "/var/lib/wirenboard/serial.conf"
PRIMARY_IFACE = "eth0"


def get_adapters():
    adapters = []
    nm = NetworkManagerAdapter.probe()
    if nm is not None:
        adapters.append(nm)
    ni = NetworkInterfacesAdapter.probe()
    if ni is not None:
        adapters.append(ni)
    return adapters


def to_json():
    interfaces = []
    ssids = []
    devices = {
        "ethernet": [],
        "wifi": [],
        "modem": []
    }
    for adapter in get_adapters():
        interfaces = interfaces + adapter.read()
        ssids = ssids + adapter.get_wifi_ssids()
        adapter.add_devices(devices)
    r = dict(interfaces=interfaces, ssids=ssids, devices=devices)
    json.dump(r, sys.stdout, sort_keys=True, indent=JSON_INDENT_LEVEL)


def from_json():
    try:
        d = json.load(sys.stdin)
    except ValueError:
        print >>sys.stdout, "Invalid JSON"
        sys.exit(1)

    interfaces = d["interfaces"]
    for adapter in get_adapters():
        interfaces = adapter.apply(interfaces)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "-s":
        from_json()
    else:
        to_json()


if __name__ == "__main__":
    main()
