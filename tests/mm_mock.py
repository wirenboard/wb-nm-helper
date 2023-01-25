import abc
import json
import logging

from wb.nm_helper.network_manager import NM_ACTIVE_CONNECTION_STATE_DEACTIVATED, \
    NM_ACTIVE_CONNECTION_STATE_ACTIVATED, NM_ACTIVE_CONNECTION_STATE_UNKNOWN, NM_ACTIVE_CONNECTION_STATE_ACTIVATING


class AbsFakeNMConnection:

    def __init__(self, name, net_man):
        self.name = name
        self.net_man = net_man

    def data(self):
        return self.net_man.connections.get(self.name)

    def get_settings(self):
        settings = {
            "connection": {
                "type": self.data().get('device_type')
            }
        }
        if self.data().get('device_type') == 'gsm':
            settings['gsm'] = {
                'sim-slot': self.data().get('sim_slot')
            }
        return settings

    def get_connection_type(self):
        return self.get_settings().get('connection').get('type')

    def get_connection_id(self):
        return self.name

    def get_ifaces(self):
        if self.data().get('connection_state') == NM_ACTIVE_CONNECTION_STATE_ACTIVATED:
            return [self.name]  # FIXME: make more accurate
        else:
            return [None]  # FIXME: make more accurate


class FakeNMActiveConnection(AbsFakeNMConnection):

    def get_property(self, param):
        if param == 'State':
            return self.data().get('connection_state')


class FakeNMConnection(AbsFakeNMConnection):

    pass


class FakeNMDevice:
    def __init__(self, con):
        self.con = con
        self.net_man: FakeNetworkManager = con.net_man
        self.index = 1

    def increase_index(self):
        self.index += 1

    def get_property(self, param):
        if param == "Udi":
            return "/fake/Devices/{}".format(self.index)

    def get_active_connection(self):
        if self.con.data().get("device_type") == "gsm":
            logging.info('connections:')
            logging.info(json.dumps(self.net_man.connections))
            for name, data in self.net_man.connections.items():
                if data.get('sim_slot') == self.net_man.gsm_sim_slot \
                        and data.get('connection_state') == NM_ACTIVE_CONNECTION_STATE_ACTIVATED:
                    logging.info('Active Device GSM connection is %s', name)
                    return FakeNMActiveConnection(name, self.net_man)
        else:
            logging.info('Active Device connection is %s', self.con.name)
            return FakeNMActiveConnection(self.con.name, self.net_man)


class FakeNetworkManager:

    def __init__(self):
        self.connections = {}
        self.gsm_sim_slot = 1
        self.modem_device = None

    def add_connection(
            self,
            name,
            device_type,
            device_connected=False,
            connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN,
            **kwargs
    ):
        self.connections[name] = {
            "device_type": device_type,
            "device_connected": device_connected,
            "connection_state": connection_state
        }
        for k, v in kwargs.items():
            self.set_connection_param(name, k, v)

    def set_connection_param(self, name, param, value):
        self.connections[name][param] = value

    def add_wifi_client(
            self,
            name,
            device_connected=False,
            connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN,
            **kwargs
    ):
        self.add_connection(
            name,
            device_type='802-11-wireless',
            device_connected=device_connected,
            connection_state=connection_state,
            **kwargs
        )

    def add_ethernet(
            self,
            name,
            device_connected=False,
            connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN,
            **kwargs
    ):
        self.add_connection(
            name,
            device_type='802-3-ethernet',
            device_connected=device_connected,
            connection_state=connection_state,
            **kwargs
        )

    def add_gsm(
            self,
            name,
            device_connected=False,
            connection_state=NM_ACTIVE_CONNECTION_STATE_UNKNOWN,
            sim_slot=1,
            **kwargs
    ):
        self.add_connection(
            name,
            device_type='gsm',
            device_connected=device_connected,
            connection_state=connection_state,
            sim_slot=sim_slot,
            **kwargs
        )

    def get_active_connections(self):
        logging.debug('get_active_connections()')
        output = {}
        for name, data in self.connections.items():
            logging.debug('Connection %s is %s', name, data.get('connection_state'))
            if data.get('connection_state') in (
                    NM_ACTIVE_CONNECTION_STATE_ACTIVATED,
                    NM_ACTIVE_CONNECTION_STATE_ACTIVATING
            ):
                output[name] = FakeNMActiveConnection(name, self)
        logging.debug(output)
        return output

    def deactivate_connection(self, con):
        name = con.get_connection_id()
        self.set_connection_param(name, 'connection_state', NM_ACTIVE_CONNECTION_STATE_DEACTIVATED)

    def find_connection(self, cn_id):
        if cn_id in self.connections:
            return FakeNMConnection(cn_id, self)

    def find_device_for_connection(self, con):
        if con.data().get('device_connected'):
            dev = FakeNMDevice(con)
            if con.data().get('device_type') == 'gsm':
                if not con.net_man.modem_device:
                    con.net_man.modem_device = dev
                    logging.info('Assigned modem_device %s', dev)
                else:
                    logging.info('Using current modem_device')
                    return con.net_man.modem_device
            return dev

    def activate_connection(self, con, dev):
        if con.name not in self.connections:
            raise Exception('No connection found: {}'.format(con.name))
        if not self.connections.get(con.name).get('device_connected'):
            self.connections[con.name]['connection_state'] = NM_ACTIVE_CONNECTION_STATE_DEACTIVATED
        if self.connections.get(con.name).get('device_type') == "gsm" \
                and self.connections.get(con.name).get('sim_slot') != self.gsm_sim_slot:
            self.connections[con.name]['connection_state'] = NM_ACTIVE_CONNECTION_STATE_UNKNOWN
        elif self.connections.get(con.name).get('should_stuck_activating'):
            self.connections[con.name]['connection_state'] = NM_ACTIVE_CONNECTION_STATE_ACTIVATING
        else:
            self.connections[con.name]['connection_state'] = NM_ACTIVE_CONNECTION_STATE_ACTIVATED
        return FakeNMActiveConnection(con.name, self)


class FakeModemManager:
    def __init__(self, net_man):
        self.net_man = net_man

    def get_primary_sim_slot(self, dev_path):
        return self.net_man.gsm_sim_slot

    def set_primary_sim_slot(self, dev_path, sim_slot):
        for name, data in self.net_man.connections.items():
            if data.get("device_type") == "gsm" and data.get("device_connected") and data.get("sim_slot") == sim_slot:
                logging.info('Set SIM slot %s', str(sim_slot))
                if self.net_man.modem_device:
                    self.net_man.modem_device.increase_index()
                self.net_man.gsm_sim_slot = sim_slot
                return True
