from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class INMObject(ABC):
    @abstractmethod
    def get_object(self):
        pass

    @abstractmethod
    def get_iface(self):
        pass

    @abstractmethod
    def get_prop_iface(self):
        pass

    @abstractmethod
    def get_property(self, property_name: str):
        pass

    @abstractmethod
    def get_path(self):
        pass


class INetworkManager(INMObject):
    @abstractmethod
    def find_connection(self, cn_id: str) -> Optional[INMConnection]:
        pass

    @abstractmethod
    def get_active_connections(self) -> Dict[str, INMActiveConnection]:
        pass

    @abstractmethod
    def find_device_by_param(self, param_name: str, param_value: str) -> Optional[INMDevice]:
        pass

    @abstractmethod
    def find_device_for_connection(self, cn_obj: INMConnection) -> Optional[INMDevice]:
        pass

    @abstractmethod
    def deactivate_connection(self, con: INMActiveConnection) -> None:
        pass

    @abstractmethod
    def get_version(self) -> str:
        pass

    @abstractmethod
    def get_devices(self) -> List[INMDevice]:
        pass

    @abstractmethod
    def get_connections(self) -> List[INMConnection]:
        pass

    @abstractmethod
    def add_connection(self, connection_settings):
        pass

    @abstractmethod
    def activate_connection(self, con: INMConnection, dev: INMDevice) -> INMActiveConnection:
        pass


class INMConnection(INMObject):
    @abstractmethod
    def get_settings(self):
        pass

    @abstractmethod
    def get_connection_type(self) -> str:
        pass

    @abstractmethod
    def delete(self):
        pass

    @abstractmethod
    def update_settings(self, settings):
        pass


class INMDevice(INMObject):
    @abstractmethod
    def set_metric(self, metric: int):
        pass

    @abstractmethod
    def get_active_connection(self) -> Optional[INMActiveConnection]:
        pass


class INMActiveConnection(INMObject):
    @abstractmethod
    def get_devices(self) -> List[INMDevice]:
        pass

    @abstractmethod
    def get_ifaces(self) -> List[str]:
        pass

    @abstractmethod
    def get_connection_id(self) -> str:
        pass

    @abstractmethod
    def get_connection_type(self) -> str:
        pass

    @abstractmethod
    def get_connection(self) -> INMConnection:
        pass

    @abstractmethod
    def get_ip4_connectivity(self):
        pass


class INMAccessPoint(INMObject, ABC):
    pass


class INMWirelessDevice(INMObject):
    @abstractmethod
    def request_wifi_scan(self) -> None:
        pass

    @abstractmethod
    def get_access_points(self) -> List[INMAccessPoint]:
        pass
