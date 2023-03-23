import enum
import logging


class ConnectionState(enum.Enum):
    Unknown = 0
    Activating = 1
    Activated = 2
    Deactivating = 3
    Deactivated = 4


class Connection:
    def __init__(self, name, uuid, type, logger: logging.Logger):
        self._logger = logger
        self._name = name
        self._uuid = uuid
        self._type = type
        self._active = False
        self._state = None
        self._device = None
        self._ip4addresses = None
        self._connectivity = False

    @property
    def name(self):
        return self._name

    @property
    def uuid(self):
        return self._uuid

    @property
    def type(self):
        return self._type

    @property
    def active(self):
        return self._active

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def device(self):
        return self._device

    @property
    def connectivity(self):
        return self._connectivity

    @property
    def ip4addresses(self):
        return self._ip4addresses

    def set_active(self, active):
        self._active = active

    def set_device(self, device):
        self._device = device

    def set_state(self, state: ConnectionState):
        self._state = state

    def set_connectivity(self, connectivity):
        self._connectivity = connectivity

    def set_ip4addresses(self, ip4addresses):
        self._ip4addresses = ip4addresses
