import dbus
import dbus.mainloop.glib
import dbus.types
from active_connection import ActiveConnection
from connection import Connection
from gi.repository import GLib


class DbusClient:
    def __init__(
        self,
        new_connection_callback,
        connection_removed_callback,
        connection_updated_callback,
        logger,
    ):
        self._new_connection_callback = new_connection_callback
        self._connection_removed_callback = connection_removed_callback
        self._connection_updated_callback = connection_updated_callback
        self._logger = logger

        self._connections = {}
        self._active_connections = {}

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        dbus.mainloop.glib.threads_init()
        self._bus = dbus.SystemBus()
        self._loop = GLib.MainLoop()

    def initialize_connections(self):
        self._read_connections_list()
        self._create_existing_connections()
        self._read_active_connections_list()
        self._set_connections_event_handlers()

    def run_event_loop(self):
        self._loop.run()

    def stop_event_loop(self):
        self._loop.quit()

    def _read_connection_settings(self, connection_path):
        proxy = self._bus.get_object("org.freedesktop.NetworkManager", connection_path)
        interface = dbus.Interface(proxy, "org.freedesktop.NetworkManager.Settings.Connection")
        settings = interface.GetSettings()

        result = {}
        result["name"] = str(settings["connection"]["id"])
        result["uuid"] = str(settings["connection"]["uuid"])
        result["type"] = str(settings["connection"]["type"])
        return result

    def _create_connection(self, connection_path):
        settings = self._read_connection_settings(connection_path)
        connection = Connection(settings["name"], settings["uuid"], settings["type"], self._logger)
        self._connections[connection_path] = connection
        self._logger.info(
            "New connection %s %s %s %s",
            settings["name"],
            settings["uuid"],
            settings["type"],
            connection_path,
        )
        return connection

    def _remove_connection(self, connection_path):
        connection = self._connections[connection_path]
        self._logger.info(
            "Remove connection %s %s %s %s",
            connection.name,
            connection.uuid,
            connection.type,
            connection_path,
        )
        self._connections.pop(connection_path)
        return connection

    def _read_connections_list(self):
        connections_settings_proxy = self._bus.get_object(
            "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager/Settings"
        )

        connections_settings_interface = dbus.Interface(
            connections_settings_proxy, "org.freedesktop.NetworkManager.Settings"
        )
        connections_paths = connections_settings_interface.ListConnections()

        for connection_path in connections_paths:
            self._create_connection(connection_path)

    def _update_connection_callback(self, connection_path, **properties):
        if "active" in properties:
            self._connections[connection_path].set_active(properties["active"])
        if "state" in properties:
            self._connections[connection_path].set_state(properties["state"])
        if "device" in properties:
            self._connections[connection_path].set_device(properties["device"])
        if "ip4addresses" in properties:
            self._connections[connection_path].set_ip4addresses(properties["ip4addresses"])
        if "connectivity" in properties:
            self._connections[connection_path].set_connectivity(properties["connectivity"])
        self._connection_updated_callback(self._connections[connection_path])

    def _create_active_connection(self, active_connection_path):
        try:
            self._active_connections[active_connection_path] = ActiveConnection(
                self._bus, active_connection_path, self._update_connection_callback, self._logger
            )
        except dbus.exceptions.DBusException:
            self._logger.error("New active connection create failed %s", active_connection_path)

    def _remove_active_connection(self, active_connection_path):
        self._active_connections[active_connection_path].deactivate()
        self._active_connections.pop(active_connection_path)

    def _read_active_connections_list(self):
        active_connections_proxy = self._bus.get_object(
            "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager"
        )
        active_connections_interface = dbus.Interface(
            active_connections_proxy, "org.freedesktop.DBus.Properties"
        )
        active_connections_paths = active_connections_interface.Get(
            "org.freedesktop.NetworkManager", "ActiveConnections"
        )

        for active_connection_path in active_connections_paths:
            self._create_active_connection(active_connection_path)

    # Signals handlers

    def _new_connection_handler(self, *args, **kwargs):
        connection_path = args[0]

        # For some reasons handler receive first signals from non-existed client
        # when you try to do something with connection (add,remove,etc).
        # Finally it receives normal messages after garbage
        if kwargs["sender"] in self._bus.list_names():
            self._new_connection_callback(self._create_connection(connection_path))
        return

    def _connection_removed_handler(self, *args, **kwargs):
        connection_path = kwargs["path"]
        if connection_path in self._connections:
            self._connection_removed_callback(self._remove_connection(connection_path))

    def _active_connection_list_update_handler(self, *args, **kwargs):
        updated_properties = args[1]
        if "ActiveConnections" in updated_properties:
            active_connections_paths = updated_properties["ActiveConnections"]
            old_active_paths = [x for x in self._active_connections if x not in active_connections_paths]
            new_active_paths = [x for x in active_connections_paths if x not in self._active_connections]

            for new_active_path in new_active_paths:
                self._create_active_connection(new_active_path)

            for old_active_path in old_active_paths:
                self._remove_active_connection(old_active_path)

    def _set_connections_event_handlers(self):
        self._bus.add_signal_receiver(
            self._new_connection_handler,
            "NewConnection",
            "org.freedesktop.NetworkManager.Settings",
            "org.freedesktop.NetworkManager",
            "/org/freedesktop/NetworkManager/Settings",
            sender_keyword="sender",
        )
        self._bus.add_signal_receiver(
            self._connection_removed_handler,
            "Removed",  # ConnectionRemoved from org.freedesktop.NetworkManager.Settings doesn't work well
            "org.freedesktop.NetworkManager.Settings.Connection",
            "org.freedesktop.NetworkManager",
            None,
            path_keyword="path",
        )
        self._bus.add_signal_receiver(
            self._active_connection_list_update_handler,
            "PropertiesChanged",
            "org.freedesktop.DBus.Properties",
            "org.freedesktop.NetworkManager",
            "/org/freedesktop/NetworkManager",
        )

    def _create_existing_connections(self):
        for connection in self._connections.values():
            self._new_connection_callback(connection)

    def connection_activity_switch(self, connection: Connection, enable):
        connections_paths = [
            path for path, exist_connection in self._connections.items() if exist_connection == connection
        ]
        if len(connections_paths) != 1:
            self._logger.error("Unable to find connection to switch")
            return

        connection_path = connections_paths[0]
        if enable:
            try:
                proxy = self._bus.get_object(
                    "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager"
                )
                interface = dbus.Interface(proxy, "org.freedesktop.NetworkManager")
                empty_proxy = self._bus.get_object("org.freedesktop.NetworkManager", "/")
                connections_paths = interface.ActivateConnection(connection_path, empty_proxy, empty_proxy)

                self._logger.info(
                    "Activate connection %s %s %s manually", connection.name, connection.uuid, connection_path
                )
            except dbus.exceptions.DBusException:
                self._logger.error(
                    "Unable to activate connection %s %s %s manually",
                    connection.name,
                    connection.uuid,
                    connection_path,
                )

        else:
            active_connections_path = [
                active_path
                for active_path, active_connection in self._active_connections.items()
                if active_connection.connection_path == connection_path
            ]
            if len(active_connections_path) != 1:
                self._logger.error("Unable to find active connection to disable")
                return

            active_connection_path = active_connections_path[0]
            proxy = self._bus.get_object("org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager")
            interface = dbus.Interface(proxy, "org.freedesktop.NetworkManager")
            connections_paths = interface.DeactivateConnection(active_connection_path)

            self._logger.info(
                "Deactivate connection %s %s %s manually",
                connection.name,
                connection.uuid,
                active_connection_path,
            )
