from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class NMObjectInterface(ABC):
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


class NetworkManagerInterface(NMObjectInterface, ABC):
    @abstractmethod
    def find_connection(self, cn_id: str) -> Optional[NMConnectionInterface]:
        pass

    @abstractmethod
    def get_active_connections(self) -> Dict[str, NMActiveConnectionInterface]:
        pass

    @abstractmethod
    def find_device_by_param(self, param_name: str, param_value: str) -> Optional[NMDeviceInterface]:
        pass

    @abstractmethod
    def find_device_for_connection(self, cn_obj: NMConnectionInterface) -> Optional[NMDeviceInterface]:
        pass

    @abstractmethod
    def deactivate_connection(self, con: NMActiveConnectionInterface) -> None:
        pass

    @abstractmethod
    def get_version(self) -> str:
        pass

    @abstractmethod
    def get_devices(self) -> List[NMDeviceInterface]:
        pass

    @abstractmethod
    def get_connections(self) -> List[NMConnectionInterface]:
        pass

    @abstractmethod
    def add_connection(self, connection_settings):
        pass

    @abstractmethod
    def activate_connection(
        self, con: NMConnectionInterface, dev: NMDeviceInterface
    ) -> NMActiveConnectionInterface:
        pass


class NMConnectionInterface(NMObjectInterface, ABC):
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


class NMDeviceInterface(NMObjectInterface, ABC):
    @abstractmethod
    def set_metric(self, metric: int):
        pass

    @abstractmethod
    def get_active_connection(self) -> Optional[NMActiveConnectionInterface]:
        pass


class NMActiveConnectionInterface(NMObjectInterface, ABC):
    @abstractmethod
    def get_devices(self) -> List[NMDeviceInterface]:
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
    def get_connection(self) -> NMConnectionInterface:
        pass

    @abstractmethod
    def get_ip4_connectivity(self):
        pass


class NMAccessPointInterface(NMObjectInterface, ABC):
    pass


class NMWirelessDeviceInterface(NMObjectInterface, ABC):
    @abstractmethod
    def request_wifi_scan(self) -> None:
        pass

    @abstractmethod
    def get_access_points(self) -> List[NMAccessPointInterface]:
        pass
