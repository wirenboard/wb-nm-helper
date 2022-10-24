from __future__ import annotations

from typing import TypedDict


class ParseError(Exception):
    pass


class DeviceDesc(TypedDict):
    type: str
    iface: str


DEVICE_TYPE_ETHERNET = "ethernet"
DEVICE_TYPE_WIFI = "wifi"
DEVICE_TYPE_MODEM = "modem"


class INetworkManagementSystem:
    """
    The base interface provides functions to read or create interfaces for specific network manager system.
    """

    @staticmethod
    def probe():
        pass

    def apply(self, _interfaces):
        pass

    def get_connections(self):
        pass

    def get_wifi_ssids(self) -> list[str]:
        return []

    def get_devices(self) -> list[DeviceDesc]:
        return []
