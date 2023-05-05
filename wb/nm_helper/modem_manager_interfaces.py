from abc import ABC, abstractmethod


class IModemManager(ABC):
    @property
    @abstractmethod
    def default_modem_path(self):
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
