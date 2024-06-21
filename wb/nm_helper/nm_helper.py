from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import sys
from typing import Dict, List

import dbus

from .network_interfaces_adapter import NetworkInterfacesAdapter
from .network_manager_adapter import NetworkManagerAdapter


def is_modem_enabled(modem_dt_alias: str) -> bool:
    dt_base = "/sys/firmware/devicetree/base"
    try:
        with open(f"{dt_base}/aliases/{modem_dt_alias}", encoding="ascii") as dt_file:
            nodepath = dt_file.read().rstrip("\x00")
            with open(f"{dt_base}{nodepath}/status", encoding="ascii") as dt_file:
                return dt_file.read().rstrip("\x00") == "okay"
    except FileNotFoundError:
        return False


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

    if not is_modem_enabled(modem_dt_alias="wbc_modem"):
        connections = [c for c in connections if not c.get("connection_id", "").startswith("wb-gsm-sim")]

    devices.sort(key=lambda v: v["type"])

    switch_cfg = {}
    try:
        with open(args.config, encoding="utf-8") as file:
            switch_cfg = json.load(file)
    except (FileNotFoundError, PermissionError, OSError, json.decoder.JSONDecodeError) as ex:
        logging.error("Loading %s failed: %s", args.config, ex)

    ssids = []
    if not args.no_scan:
        try:
            ssids = network_manager.get_wifi_ssids(datetime.timedelta(seconds=args.scan_timeout))
        except dbus.exceptions.DBusException as ex:
            logging.info("Error during Wi-Fi scan: %s", ex)

    return {
        "ui": {"connections": connections, "con_switch": switch_cfg},
        "data": {
            "ssids": ssids,
            "devices": devices,
            "wifi_bands": network_manager.get_wifi_bands(),
        },
    }


def get_systemd_manager(dry_run: bool):
    """Returns a Systemd manager object

    :param dry_run: if True, a dummy object will be returned
    :type dry_run: bool
    :returns: a Systemd manager object
    :rtype: dbus.Interface
    """

    if dry_run:
        return type(
            "Systemd",
            (object,),
            {"StopUnit": lambda self, name, mode: None, "RestartUnit": lambda self, name, mode: None},
        )()
    system_bus = dbus.SystemBus()
    systemd1 = system_bus.get_object("org.freedesktop.systemd1", "/org/freedesktop/systemd1")
    return dbus.Interface(systemd1, "org.freedesktop.systemd1.Manager")


def apply_network_interfaces(connections, args, manager):
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

        if apply_res.is_changed:
            manager.RestartUnit("networking.service", "fail")

        connections = apply_res.unmanaged_connections

    return released_interfaces, connections


def apply_network_manager(connections, released_interfaces, args, manager):
    network_manager = NetworkManagerAdapter.probe()
    if network_manager is not None:
        # wb-connection-manager will be later restarted by wb-mqtt-confed
        manager.StopUnit("wb-connection-manager.service", "fail")
        res = network_manager.apply(connections, args.dry_run)

        # NetworkManager must be restarted to update managed devices
        if res or len(released_interfaces) > 0:
            manager.RestartUnit("NetworkManager.service", "fail")


def from_json(cfg, args) -> Dict:
    connections = cfg["ui"]["connections"]
    manager = get_systemd_manager(args.dry_run)

    released_interfaces, connections = apply_network_interfaces(connections, args, manager)
    apply_network_manager(connections, released_interfaces, args, manager)

    return cfg["ui"].get("con_switch", {})


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NM helper", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("-s", "--save", action="store_true", help="Save configuration")
    parser.add_argument(
        "-c", "--config", type=str, default="/etc/wb-connection-manager.conf", help="Config file"
    )
    parser.add_argument("--dnsmasq-conf", type=str, default="/etc/dnsmasq.conf", help="dnsmasq config file")
    parser.add_argument("--hostapd-conf", type=str, default="/etc/hostapd.conf", help="hostapd config file")
    parser.add_argument(
        "--interfaces-conf", type=str, default="/etc/network/interfaces", help="interfaces config file"
    )
    parser.add_argument("--no-scan", action="store_true", help="Don't scan for Wi-Fi networks")
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
