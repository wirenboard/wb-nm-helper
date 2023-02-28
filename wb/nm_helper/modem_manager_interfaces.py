from abc import ABC, abstractmethod


class IModemManager(ABC):
    @abstractmethod
    def get_modem(self, modem_path):
        pass

    @abstractmethod
    def get_primary_sim_slot(self, modem_path):
        pass

    @abstractmethod
    def set_primary_sim_slot(self, modem_path, slot_index):
        pass
