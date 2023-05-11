from abc import ABC, abstractmethod


class IModemManager(ABC):
    @property
    @abstractmethod
    def wb_specific_property(self):
        pass

    @abstractmethod
    def get_modem_prop(self, modem_iface, propname):
        pass

    @abstractmethod
    def get_modem(self, modem_path=None):
        pass

    @abstractmethod
    def get_primary_sim_slot(self, modem_path=None):
        pass

    @abstractmethod
    def set_primary_sim_slot(self, slot_index, modem_path=None):
        pass
