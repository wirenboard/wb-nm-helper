import dbus


class MMObject:
    def __init__(self, path: str, bus: dbus.SystemBus, interface_name: str):
        self.path = path
        self.bus = bus
        self.interface_name = interface_name
        self.obj = None
        self.iface = None
        self.prop_iface = None

    def get_object(self):
        if self.obj is None:
            self.obj = self.bus.get_object("org.freedesktop.ModemManager1", self.path)
        return self.obj

    def get_iface(self):
        if self.iface is None:
            self.iface = dbus.Interface(self.get_object(), self.interface_name)
        return self.iface

    def get_prop_iface(self):
        if self.prop_iface is None:
            self.prop_iface = dbus.Interface(self.get_object(), "org.freedesktop.DBus.Properties")
        return self.prop_iface

    def get_property(self, property_name: str):
        return self.get_prop_iface().Get(self.interface_name, property_name)

    def get_path(self) -> str:
        return self.path


class MMModem(MMObject):
    def __init__(self, path: str, bus: dbus.SystemBus):
        MMObject.__init__(self, path, bus, "org.freedesktop.ModemManager1.Modem")

    def get_primary_sim_slot(self) -> int:
        return self.get_property("PrimarySimSlot")

    def get_id(self) -> str:
        return self.get_property("DeviceIdentifier")

    def set_primary_sim_slot(self, slot_index: int) -> None:
        self.get_iface().SetPrimarySimSlot(slot_index)
