import dbus

from wb.nm_helper.network_manager import NMObject


class MMObject(NMObject):
    def __init__(self, path: str, bus: dbus.SystemBus, interface_name: str):
        NMObject.__init__(self, path, bus, interface_name)
        self.dbus_name = "org.freedesktop.ModemManager1"


class MMModem(MMObject):
    def __init__(self, path: str, bus: dbus.SystemBus):
        MMObject.__init__(self, path, bus, "org.freedesktop.ModemManager1.Modem")

    def get_primary_sim_slot(self) -> int:
        return self.get_property("PrimarySimSlot")

    def get_id(self) -> str:
        return self.get_property("DeviceIdentifier")

    def set_primary_sim_slot(self, slot_index: int) -> None:
        self.get_iface().SetPrimarySimSlot(slot_index)
