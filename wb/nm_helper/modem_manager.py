import dbus
import logging


class ModemManager:
    # pylint: disable=too-few-public-methods
    def __init__(self):
        self.bus = dbus.SystemBus()
        self.mm_proxy = self.bus.get_object("org.freedesktop.ModemManager1", "/org/freedesktop/ModemManager1")

    def set_primary_sim_slot(self, modem_path, slot_index):
        objects = dbus.Interface(self.mm_proxy, "org.freedesktop.DBus.ObjectManager")
        for obj in objects.GetManagedObjects():
            if obj == modem_path:
                modem_proxy = self.bus.get_object("org.freedesktop.ModemManager1", obj)
                modem = dbus.Interface(modem_proxy, "org.freedesktop.ModemManager1.Modem")
                modem_properties = dbus.Interface(modem, "org.freedesktop.DBus.Properties")
                current_sim = modem_properties.Get("org.freedesktop.ModemManager1.Modem", "PrimarySimSlot")
                if current_sim == slot_index:
                    logging.debug('Sim slot is already set to {}, no need for any changes'.format(current_sim))
                    return True
                modem.SetPrimarySimSlot(slot_index)
                return True
        return False
