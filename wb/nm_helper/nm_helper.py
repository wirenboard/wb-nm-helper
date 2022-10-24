import json
import logging
import sys

from .network_interfaces_adapter import NetworkInterfacesAdapter
from .network_manager_adapter import NetworkManagerAdapter

JSON_INDENT_LEVEL = 2
CONNECTION_MANAGER_CONFIG_FILE = "/etc/wb-connection-manager.conf"


def get_adapters():
    adapters = []
    network_manager = NetworkManagerAdapter.probe()
    if network_manager is not None:
        adapters.append(network_manager)
    network_interfaces = NetworkInterfacesAdapter.probe()
    if network_interfaces is not None:
        adapters.append(network_interfaces)
    return adapters


def to_json():
    connections = []
    ssids = []
    devices = []
    for adapter in get_adapters():
        connections = connections + adapter.get_connections()
        ssids = ssids + adapter.get_wifi_ssids()
        devices = devices + adapter.get_devices()

    devices.sort(key=lambda v: v["type"])

    switch_cfg = {}
    try:
        with open(CONNECTION_MANAGER_CONFIG_FILE, encoding="utf-8") as file:
            switch_cfg = json.load(file)
    except (FileNotFoundError, PermissionError, OSError, json.decoder.JSONDecodeError) as ex:
        logging.error("Loading %s failed: %s", CONNECTION_MANAGER_CONFIG_FILE, ex)

    res = {
        "ui": {"connections": connections, "con_switch": switch_cfg},
        "data": {"ssids": ssids, "devices": devices},
    }
    json.dump(res, sys.stdout, sort_keys=True, indent=JSON_INDENT_LEVEL)


def from_json():
    try:
        cfg = json.load(sys.stdin)
    except ValueError:
        print("Invalid JSON", file=sys.stdout)
        sys.exit(1)

    connections = cfg["ui"]["connections"]
    for adapter in get_adapters():
        connections = adapter.apply(connections)
    json.dump(cfg["ui"]["con_switch"], sys.stdout, sort_keys=True, indent=JSON_INDENT_LEVEL)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "-s":
        from_json()
    else:
        to_json()


if __name__ == "__main__":
    main()
