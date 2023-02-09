import dbus

from wb.nm_helper.modem_manager_interfaces import ModemManagerInterface


class ModemManager(ModemManagerInterface):
    def __init__(self):
        self.bus = dbus.SystemBus()
        self.mm_proxy = self.bus.get_object("org.freedesktop.ModemManager1", "/org/freedesktop/ModemManager1")

    def get_modem(self, modem_path):
        objects = dbus.Interface(self.mm_proxy, "org.freedesktop.DBus.ObjectManager")
        for obj in objects.GetManagedObjects():
            if obj == modem_path:
                modem_proxy = self.bus.get_object("org.freedesktop.ModemManager1", obj)
                modem = dbus.Interface(modem_proxy, "org.freedesktop.ModemManager1.Modem")
                return modem
        return None

    def get_primary_sim_slot(self, modem_path):
        modem = self.get_modem(modem_path)
        if modem:
            modem_properties = dbus.Interface(modem, "org.freedesktop.DBus.Properties")
            current_sim = modem_properties.Get("org.freedesktop.ModemManager1.Modem", "PrimarySimSlot")
            return current_sim
        return None

    def set_primary_sim_slot(self, modem_path, slot_index):
        modem = self.get_modem(modem_path)
        if modem:
            modem.SetPrimarySimSlot(slot_index)
            return True
        return False
