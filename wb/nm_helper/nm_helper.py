from __future__ import annotations

import argparse
import datetime
import dbus
import json
import logging
import re
import subprocess
import sys
from typing import List

from .network_interfaces_adapter import NetworkInterfacesAdapter
from .network_manager_adapter import NetworkManagerAdapter


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


"""Scans for Wi-Fi networks

:param iface - interface to scan
:type iface: str
:param timeout_s - timeout in seconds (if the timeout expires, the iwlist process will be killed)
:type timeout_s: int
:returns: a list of ESSID names or an empty list if scan failed
:rtype: list
"""
def scan_wifi(iface: str, timeout_s: int) -> List[str]:
    res = []
    try:
        pattern = re.compile(r"ESSID:\s*\"(.*)\"")
        scan_result = subprocess.check_output(
            ["iwlist", iface, "scan"], timeout=datetime.timedelta(seconds=timeout_s).total_seconds(), text=True
        )
        for line in scan_result.splitlines():
            match = pattern.search(line)
            if match and match.group(1):
                res.append(match.group(1))
        res = sorted(set(res))
    except subprocess.TimeoutExpired:
        logging.info("Can't get Wi-Fi scanning results within %d", timeout_s)
    except subprocess.CalledProcessError as ex:
        logging.info("Error during Wi-Fi scan: %s", ex)
    return res


def to_json(args) -> Dict:
    connections = []
    devices = []

    network_manager = NetworkManagerAdapter.probe()
    if network_manager is not None:
        connections = network_manager.get_connections()
        devices = network_manager.get_devices()

    network_interfaces = NetworkInterfacesAdapter.probe(args.interfaces_conf)
    if network_interfaces is not None:
        connections = connections + network_interfaces.get_connections()

    devices.sort(key=lambda v: v["type"])

    switch_cfg = {}
    try:
        with open(args.config, encoding="utf-8") as file:
            switch_cfg = json.load(file)
    except (FileNotFoundError, PermissionError, OSError, json.decoder.JSONDecodeError) as ex:
        logging.error("Loading %s failed: %s", args.config, ex)

    return {
        "ui": {"connections": connections, "con_switch": switch_cfg},
        "data": {"ssids": [] if args.no_scan else scan_wifi(args.scan_iface, args.scan_timeout), "devices": devices},
    }


"""Returns a Systemd manager object

:param dry_run: if True, a dummy object will be returned
:type dry_run: bool
:returns: a Systemd manager object
:rtype: dbus.Interface
"""
def get_systemd_manager(dry_run: bool):
    if dry_run:
        return type("Systemd", (object,), {
            "StopUnit": lambda self, name, mode: None,
            "RestartUnit": lambda self, name, mode: None
        })()
    else:
        system_bus = dbus.SystemBus()
        systemd1 = system_bus.get_object('org.freedesktop.systemd1', '/org/freedesktop/systemd1')
        return dbus.Interface(systemd1, 'org.freedesktop.systemd1.Manager')


def from_json(cfg, args) -> Dict:
    connections = cfg["ui"]["connections"]

    manager = get_systemd_manager(args.dry_run)

    managed_interfaces = []
    released_interfaces = []
    network_interfaces = NetworkInterfacesAdapter.probe(args.interfaces_conf)
    if network_interfaces is not None:
        apply_res = network_interfaces.apply(connections, args.dry_run)
        managed_interfaces = apply_res.managed_interfaces
        released_interfaces = apply_res.released_interfaces
        # NM conflicts with dnsmasq and hostapd
        # Stop them if wlan is not configured in /etc/network/interfaces
        managed_wlans = [iface for iface in managed_interfaces if iface.startswith("wlan")]
        if not_fully_contains(managed_wlans, find_interface_strings(args.dnsmasq_conf)):
            manager.StopUnit("dnsmasq.service", "fail")
        if not_fully_contains(managed_wlans, find_interface_strings(args.hostapd_conf)):
            manager.StopUnit("hostapd.service", "fail")

        manager.RestartUnit("networking.service", "fail")

        connections = apply_res.unmanaged_connections

    network_manager = NetworkManagerAdapter.probe()
    if network_manager is not None:
        # wb-connection-manager will be later restarted by wb-mqtt-confed
        manager.StopUnit("wb-connection-manager.service", "fail")
        network_manager.apply(connections, args.dry_run)

        # NetworkManager must be restarted to update managed devices
        if len(released_interfaces) > 0:
            manager.RestartUnit("NetworkManager.service", "fail")

    return cfg["ui"]["con_switch"]


def main() -> None:
    parser = argparse.ArgumentParser(description="NM helper", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("-s", "--save", action="store_true", help="Save configuration")
    parser.add_argument("-c", "--config", type=str, default="/etc/wb-connection-manager.conf", help="Config file")
    parser.add_argument("--dnsmasq-conf", type=str, default="/etc/dnsmasq.conf", help="dnsmasq config file")
    parser.add_argument("--hostapd-conf", type=str, default="/etc/hostapd.conf", help="hostapd config file")
    parser.add_argument("--interfaces-conf", type=str, default="/etc/network/interfaces", help="interfaces config file")
    parser.add_argument("--no-scan", action="store_true", help="Don't scan for Wi-Fi networks")
    parser.add_argument("--scan-iface", type=str, default="wlan0", help="Interface to scan for Wi-Fi networks")
    parser.add_argument("--scan-timeout", type=int, default=10, help="Scan timeout in seconds")
    parser.add_argument("--indent", type=int, default=2, help="Indentation level for JSON output")
    parser.add_argument("--dry-run", action="store_true", help="Don't apply changes")
    args = parser.parse_args()

    res = None
    if args.save:
        try:
            cfg = json.load(sys.stdin)
        except ValueError:
            print("Invalid JSON", file=sys.stdout)
            sys.exit(1)
        res = from_json(cfg, args)
    else:
        res = to_json(args)
    json.dump(res, sys.stdout, sort_keys=True, indent=args.indent)


if __name__ == "__main__":
    main()
