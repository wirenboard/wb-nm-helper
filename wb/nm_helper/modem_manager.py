from collections import namedtuple

import dbus

from wb.nm_helper.modem_manager_interfaces import IModemManager


class ModemManager(IModemManager):
    def __init__(self):
        self.bus = dbus.SystemBus()
        self.mm_proxy = self.bus.get_object("org.freedesktop.ModemManager1", "/org/freedesktop/ModemManager1")

    @property
    def wb_specific_property(self):
        return namedtuple("device_property", "prop_name prop_value")(prop_name="Device", prop_value="wbc")

    def _get_modem_iface(self, modem_path):
        modem_proxy = self.bus.get_object("org.freedesktop.ModemManager1", modem_path)
        return dbus.Interface(modem_proxy, "org.freedesktop.ModemManager1.Modem")

    def _get_modem_by_path(self, modem_path):
        objects = dbus.Interface(self.mm_proxy, "org.freedesktop.DBus.ObjectManager")
        for obj in objects.GetManagedObjects():
            if obj == modem_path:
                return self._get_modem_iface(obj)
        return None

    def _get_modem_by_default_prop(self):
        objects = dbus.Interface(self.mm_proxy, "org.freedesktop.DBus.ObjectManager")
        for obj in objects.GetManagedObjects():
            modem = self._get_modem_iface(obj)
            if self.get_modem_prop(modem, "Device") == "wbc":
                return modem
        return None

    def get_modem_prop(self, modem_iface, propname):
        modem_properties = dbus.Interface(modem_iface, "org.freedesktop.DBus.Properties")
        return modem_properties.Get("org.freedesktop.ModemManager1.Modem", propname)

    def get_modem(self, modem_path=None):
        if modem_path:
            return self._get_modem_by_path(modem_path)
        return self._get_modem_by_default_prop()

    def get_primary_sim_slot(self, modem_path=None):
        modem = self.get_modem(modem_path)
        if modem:
            return self.get_modem_prop(modem, "PrimarySimSlot")
        return None

    def set_primary_sim_slot(self, slot_index, modem_path=None):
        modem = self.get_modem(modem_path)
        if modem:
            modem.SetPrimarySimSlot(slot_index)
            return True
        return False
