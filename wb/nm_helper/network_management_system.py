from __future__ import annotations


class ParseError(Exception):
    pass


class INetworkManagementSystem:
    """
    The base interface provides functions to read or create interfaces for specific network manager system.
    """

    @staticmethod
    def probe():
        pass

    def apply(self, _interfaces):
        pass

    def read(self):
        pass

    def get_wifi_ssids(self) -> list[str]:
        return []

    def add_devices(self, _devices: list[str]) -> list[str]:
        return []
