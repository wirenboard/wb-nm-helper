class ModemManagerInterface:
    def get_modem(self, modem_path):
        raise NotImplementedError

    def get_primary_sim_slot(self, modem_path):
        raise NotImplementedError

    def set_primary_sim_slot(self, modem_path, slot_index):
        raise NotImplementedError
