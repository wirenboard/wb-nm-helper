from __future__ import annotations

import datetime
import json
import logging
import os
import re
import subprocess
import sys
from typing import List

from .network_interfaces_adapter import NetworkInterfacesAdapter
from .network_manager_adapter import NetworkManagerAdapter

WIFI_SCAN_TIMEOUT = datetime.timedelta(seconds=10)

JSON_INDENT_LEVEL = 2
CONNECTION_MANAGER_CONFIG_FILE = "/etc/wb-connection-manager.conf"


def find_interface_strings(file_name: str) -> List[str]:
    res = []
    pattern = re.compile(r"^\s*interface\s*=\s*(.*)")
    try:
        with open(file_name, "r", encoding="utf-8") as file:
            for line in file.readlines():
                match = pattern.search(line.strip())
                if match and match.group(1):
                    res.append(match.group(1))
    except FileNotFoundError:
        pass
    return res


def not_fully_contains(dst: List[str], src: List[str]) -> bool:
    for item in src:
        if item not in dst:
            return True
    return False


def scan_wifi() -> List[str]:
    res = []
    try:
        pattern = re.compile(r"ESSID:\s*\"(.*)\"")
        scan_result = subprocess.check_output(
            ["iwlist", "wlan0", "scan"], timeout=WIFI_SCAN_TIMEOUT.total_seconds(), text=True
        )
        for line in scan_result.splitlines():
            match = pattern.search(line)
            if match and match.group(1):
                res.append(match.group(1))
        res = sorted(set(res))
    except subprocess.TimeoutExpired:
        logging.info("Can't get Wi-Fi scanning results within %s", str(WIFI_SCAN_TIMEOUT))
    except subprocess.CalledProcessError as ex:
        logging.info("Error during Wi-Fi scan: %s", ex)
    return res


def to_json():
    connections = []
    devices = []

    network_manager = NetworkManagerAdapter.probe()
    if network_manager is not None:
        connections = network_manager.get_connections()
        devices = network_manager.get_devices()

    network_interfaces = NetworkInterfacesAdapter.probe()
    if network_interfaces is not None:
        connections = connections + network_interfaces.get_connections()

    devices.sort(key=lambda v: v["type"])

    switch_cfg = {}
    try:
        with open(CONNECTION_MANAGER_CONFIG_FILE, encoding="utf-8") as file:
            switch_cfg = json.load(file)
    except (FileNotFoundError, PermissionError, OSError, json.decoder.JSONDecodeError) as ex:
        logging.error("Loading %s failed: %s", CONNECTION_MANAGER_CONFIG_FILE, ex)

    res = {
        "ui": {"connections": connections, "con_switch": switch_cfg},
        "data": {"ssids": scan_wifi(), "devices": devices},
    }
    json.dump(res, sys.stdout, sort_keys=True, indent=JSON_INDENT_LEVEL)


def from_json():
    try:
        cfg = json.load(sys.stdin)
    except ValueError:
        print("Invalid JSON", file=sys.stdout)
        sys.exit(1)

    connections = cfg["ui"]["connections"]

    network_interfaces = NetworkInterfacesAdapter.probe()
    if network_interfaces is not None:
        apply_res = network_interfaces.apply(connections)
        # NM conflicts with dnsmasq and hostapd
        # Stop them if wlan is not configured in /etc/network/interfaces
        if not_fully_contains(apply_res.managed_wlans, find_interface_strings("/etc/dnsmasq.conf")):
            os.system("systemctl stop dnsmasq")
        if not_fully_contains(apply_res.managed_wlans, find_interface_strings("/etc/hostapd.conf")):
            os.system("systemctl stop hostapd")
        os.system("systemctl restart networking")

        connections = apply_res.unmanaged_connections

    network_manager = NetworkManagerAdapter.probe()
    if network_manager is not None:
        # wb-connection-manager will be later restarted by wb-mqtt-confed
        os.system("systemctl stop wb-connection-manager")
        network_manager.apply(connections)
        # NetworkManager must be restarted to update managed devices
        os.system("systemctl restart NetworkManager")
    json.dump(cfg["ui"]["con_switch"], sys.stdout, sort_keys=True, indent=JSON_INDENT_LEVEL)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "-s":
        from_json()
    else:
        to_json()


if __name__ == "__main__":
    main()
