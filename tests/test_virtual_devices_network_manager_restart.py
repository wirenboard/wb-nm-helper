# pylint: disable=protected-access
import asyncio
import unittest
from unittest.mock import MagicMock, patch

import dbus

from wb.nm_helper import virtual_devices, wbmqtt

OWNER = ":1.42"


def make_mediator():
    mediator = virtual_devices.ConnectionsMediator.__new__(virtual_devices.ConnectionsMediator)
    mediator._active_connections = {}
    mediator._common_connections = {}
    mediator._mqtt_client = MagicMock()
    mediator._bus = MagicMock()
    mediator._connectivity_updater = MagicMock(generation=1)
    mediator._event_loop = MagicMock()
    mediator._network_manager_owner = OWNER
    mediator._network_manager_restore_retries = 0
    mediator._network_manager_restore_handle = None
    mediator._active_list_reload_pending = False
    mediator._delayed_events = set()
    mediator._request_network_manager_listing = MagicMock()  # to the GLib thread
    mediator.new_event = MagicMock()
    mediator._dbus_loop = MagicMock()
    mediator.failed = False
    return mediator


def make_active(connection_path, connection_state=virtual_devices.ConnectionState.ACTIVATED):
    active = MagicMock(connection_path=connection_path)
    active.state = virtual_devices.MqttConnectionState(active=True, connection_state=connection_state)
    return active


def posted_events(mediator):
    return [call.args[0] for call in mediator.new_event.call_args_list]


class NetworkManagerOwnerTest(unittest.TestCase):
    def setUp(self):
        self.mediator = make_mediator()

    def test_owner_disappearance_drops_stale_object_paths(self):
        active = MagicMock()
        common = MagicMock()
        self.mediator._active_connections["/active/1"] = active
        self.mediator._common_connections["/settings/1"] = common

        self.mediator._network_manager_owner_changed("")

        active.stop.assert_called_once_with()
        common.stop.assert_called_once_with()
        self.assertEqual({}, self.mediator._active_connections)
        self.assertEqual({}, self.mediator._common_connections)
        self.mediator._connectivity_updater.reset.assert_called_once_with()
        self.mediator._request_network_manager_listing.assert_not_called()

    def test_new_owner_reenumerates_connections(self):
        self.mediator._network_manager_owner_changed(":1.43")

        self.mediator._request_network_manager_listing.assert_called_once_with(":1.43", restore=True)

    @patch.object(virtual_devices.ConnectionsMediator, "_active_connections_list_update")
    @patch.object(virtual_devices.ConnectionsMediator, "_common_connection_create")
    def test_listed_objects_are_created(self, common_create, active_list_update):
        self.mediator._network_manager_listed(OWNER, True, connections=["/settings/1", "/settings/2"])
        self.mediator._network_manager_listed(OWNER, True, active_connections=["/active/1"])

        self.assertEqual(["/settings/1", "/settings/2"], [c.args[0] for c in common_create.call_args_list])
        active_list_update.assert_called_once_with(["/active/1"], 0)

    def test_owner_change_is_serialized_as_an_event(self):
        self.mediator._network_manager_owner_changed_handler(
            virtual_devices.NETWORK_MANAGER_DBUS_NAME, ":1.1", ":1.2"
        )

        event = self.mediator.new_event.call_args.args[0]
        self.assertEqual(virtual_devices.EventType.NETWORK_MANAGER_OWNER_CHANGED, event.type)
        self.assertEqual(":1.2", event.kwargs["new_owner"])

    def test_same_owner_is_not_restored_again(self):
        common = MagicMock()
        self.mediator._common_connections["/settings/1"] = common

        self.mediator._network_manager_owner_changed(OWNER)

        common.stop.assert_not_called()
        self.mediator._request_network_manager_listing.assert_not_called()

    def test_owner_change_cancels_delayed_events_and_resets_retries(self):
        handle = MagicMock()
        self.mediator._delayed_events = {handle}
        self.mediator._network_manager_restore_handle = handle
        self.mediator._network_manager_restore_retries = 3
        self.mediator._active_list_reload_pending = True

        self.mediator._network_manager_owner_changed("")

        handle.cancel.assert_called_once_with()
        self.assertEqual(set(), self.mediator._delayed_events)
        self.assertIsNone(self.mediator._network_manager_restore_handle)
        self.assertEqual(0, self.mediator._network_manager_restore_retries)
        self.assertFalse(self.mediator._active_list_reload_pending)


class NetworkManagerStartupTest(unittest.TestCase):
    def setUp(self):
        self.mediator = make_mediator()
        self.mediator._dbus_loop = MagicMock()

    def test_running_network_manager_is_restored_through_owner_change(self):
        self.mediator._bus.get_name_owner.return_value = OWNER

        self.mediator.run()

        event = self.mediator.new_event.call_args.args[0]
        self.assertEqual(virtual_devices.EventType.NETWORK_MANAGER_OWNER_CHANGED, event.type)
        self.assertEqual(OWNER, event.kwargs["new_owner"])
        self.mediator._dbus_loop.run.assert_called_once_with()

    def test_missing_network_manager_is_awaited(self):
        self.mediator._bus.get_name_owner.side_effect = dbus.exceptions.DBusException(
            "no owner", name="org.freedesktop.DBus.Error.NameHasNoOwner"
        )

        self.mediator.run()

        self.mediator.new_event.assert_not_called()
        self.mediator._dbus_loop.run.assert_called_once_with()

    def test_other_dbus_errors_at_startup_are_raised(self):
        self.mediator._bus.get_name_owner.side_effect = dbus.exceptions.DBusException(
            "timeout", name="org.freedesktop.DBus.Error.NoReply"
        )

        with self.assertRaises(dbus.exceptions.DBusException):
            self.mediator.run()

        self.mediator._dbus_loop.run.assert_not_called()


class NetworkManagerRestoreRetryTest(unittest.TestCase):
    def setUp(self):
        self.mediator = make_mediator()

    def test_restore_is_retried_while_network_manager_is_not_ready(self):
        self.mediator._network_manager_listed(OWNER, True, error=dbus.exceptions.DBusException("NoReply"))

        delay, retry = self.mediator._event_loop.call_later.call_args.args
        self.assertEqual(1, delay)
        retry()
        event = self.mediator.new_event.call_args.args[0]
        self.assertEqual(virtual_devices.EventType.NETWORK_MANAGER_RESTORE, event.type)

        self.mediator._restore_network_manager_connections(**event.kwargs)

        self.mediator._request_network_manager_listing.assert_called_once_with(OWNER, restore=True)
        self.assertIsNone(self.mediator._network_manager_restore_handle)

    @patch.object(virtual_devices.GLib, "idle_add", MagicMock())
    def test_retries_are_limited(self):
        delays = []
        for _ in range(virtual_devices.NETWORK_MANAGER_RESTORE_MAX_RETRIES + 2):
            self.mediator._retry_network_manager_restore("test")
            if self.mediator._event_loop.call_later.called:
                delays.append(self.mediator._event_loop.call_later.call_args.args[0])
                self.mediator._event_loop.call_later.reset_mock()
            self.mediator._network_manager_restore_handle = None

        self.assertEqual([1, 2, 4, 8, 16], delays)

    def test_successful_listing_renews_the_budget(self):
        self.mediator._network_manager_restore_retries = 4

        self.mediator._network_manager_listed(OWNER, True, connections=[])
        self.assertEqual(4, self.mediator._network_manager_restore_retries)  # the active list may still fail
        self.mediator._network_manager_listed(OWNER, True, active_connections=[])

        self.assertEqual(0, self.mediator._network_manager_restore_retries)

    @patch.object(virtual_devices.GLib, "idle_add")
    def test_service_fails_when_retries_are_spent(self, idle_add):
        for _ in range(virtual_devices.NETWORK_MANAGER_RESTORE_MAX_RETRIES + 2):
            self.mediator._retry_network_manager_restore("test")
            self.mediator._network_manager_restore_handle = None

        self.assertTrue(self.mediator.failed)
        idle_add.assert_called_once_with(self.mediator._dbus_loop.quit)

    def test_only_one_retry_is_pending(self):
        self.mediator._retry_network_manager_restore("test")
        self.mediator._retry_network_manager_restore("test")

        self.mediator._event_loop.call_later.assert_called_once()

    def test_stale_retry_is_ignored(self):
        self.mediator._restore_network_manager_connections(":1.41")

        self.mediator._request_network_manager_listing.assert_not_called()

    @patch.object(virtual_devices.ConnectionsMediator, "_common_connection_create")
    def test_stale_listing_is_ignored(self, common_create):
        self.mediator._network_manager_listed(":1.41", True, error=dbus.exceptions.DBusException("NoReply"))
        self.mediator._network_manager_listed(":1.41", True, connections=["/settings/1"])

        self.mediator._event_loop.call_later.assert_not_called()
        common_create.assert_not_called()


class UnreadableObjectRetryTest(unittest.TestCase):
    def setUp(self):
        self.mediator = make_mediator()
        self.unreadable = dbus.exceptions.DBusException(
            "no such object", name="org.freedesktop.DBus.Error.UnknownObject"
        )
        self.timeout = dbus.exceptions.DBusException("timeout", name=virtual_devices.DBUS_NO_REPLY)

    def delayed_event(self):
        delay, post = self.mediator._event_loop.call_later.call_args.args
        post()
        return delay, self.mediator.new_event.call_args.args[0]

    @patch.object(virtual_devices, "CommonConnection")
    def test_common_connection_is_retried(self, common_connection_class):
        common_connection_class.return_value.run.side_effect = self.unreadable

        self.mediator._common_connection_create("/settings/1")

        self.assertEqual({}, self.mediator._common_connections)
        delay, event = self.delayed_event()
        self.assertEqual(1, delay)
        self.assertEqual(virtual_devices.EventType.COMMON_CREATE, event.type)
        self.assertEqual({"path": "/settings/1", "retry": 1}, event.kwargs)
        self.assertEqual(set(), self.mediator._delayed_events)

    @patch.object(virtual_devices, "CommonConnection")
    def test_common_connection_retries_are_limited_and_never_fail_the_service(self, common_connection_class):
        common_connection_class.return_value.run.side_effect = self.unreadable

        self.mediator._common_connection_create("/settings/1", retry=virtual_devices.OBJECT_READ_MAX_RETRIES)

        self.mediator._event_loop.call_later.assert_not_called()
        self.assertFalse(self.mediator.failed)

    @patch.object(virtual_devices, "CommonConnection")
    def test_timed_out_read_is_not_repeated(self, common_connection_class):
        """A call that timed out in the event loop's thread may corrupt the heap: not once more."""
        common_connection_class.return_value.run.side_effect = self.timeout

        self.mediator._common_connection_create("/settings/1")

        self.mediator._event_loop.call_later.assert_not_called()
        self.assertEqual({}, self.mediator._common_connections)

    @patch.object(virtual_devices, "ActiveConnection")
    def test_timed_out_active_connection_is_not_read_again(self, active_connection_class):
        active_connection_class.return_value.run.side_effect = self.timeout

        self.mediator._active_connections_list_update(["/active/1"])

        self.mediator._event_loop.call_later.assert_not_called()
        self.assertFalse(self.mediator._active_list_reload_pending)
        self.assertFalse(self.mediator.failed)

    def test_no_list_reload_without_network_manager(self):
        self.mediator._network_manager_owner = ""

        self.mediator._active_connections_list_update(retry=1)

        self.mediator._request_network_manager_listing.assert_not_called()

    @patch.object(virtual_devices, "ActiveConnection")
    def test_active_connections_list_is_read_again(self, active_connection_class):
        active_connection_class.return_value.run.side_effect = self.unreadable

        self.mediator._active_connections_list_update(["/active/1"])

        self.mediator._request_network_manager_listing.assert_not_called()  # not before the delay
        delay, event = self.delayed_event()
        self.assertEqual(1, delay)
        self.assertEqual(virtual_devices.EventType.ACTIVE_LIST_UPDATE, event.type)
        self.assertEqual({"retry": 1}, event.kwargs)

    @patch.object(virtual_devices, "ActiveConnection")
    def test_only_one_list_reload_is_pending(self, active_connection_class):
        active_connection_class.return_value.run.side_effect = self.unreadable

        self.mediator._active_connections_list_update(["/active/1"])
        self.mediator._active_connections_list_update(["/active/2"])

        self.mediator._event_loop.call_later.assert_called_once()

    @patch.object(virtual_devices, "ActiveConnection")
    def test_short_lived_active_connection_is_not_read_again(self, active_connection_class):
        self.mediator._active_list_reload_pending = True

        self.mediator._active_connections_list_update(retry=1)
        self.mediator._request_network_manager_listing.assert_called_once_with(OWNER, restore=False, retry=1)
        self.mediator._network_manager_listed(OWNER, False, retry=1, active_connections=[])

        active_connection_class.assert_not_called()
        self.mediator._event_loop.call_later.assert_not_called()
        self.assertFalse(self.mediator._active_list_reload_pending)

    @patch.object(virtual_devices, "ActiveConnection")
    def test_listed_active_connection_is_read_again(self, active_connection_class):
        active = make_active("/settings/1")
        active_connection_class.return_value = active

        self.mediator._network_manager_listed(OWNER, False, retry=1, active_connections=["/active/1"])

        self.assertEqual({"/active/1": active}, self.mediator._active_connections)

    def test_failed_list_reload_never_fails_the_service(self):
        self.mediator._active_list_reload_pending = True

        self.mediator._network_manager_listed(OWNER, False, retry=1, error=self.unreadable)

        self.assertFalse(self.mediator._active_list_reload_pending)
        self.mediator._event_loop.call_later.assert_not_called()
        self.assertFalse(self.mediator.failed)

    @patch.object(virtual_devices, "ActiveConnection")
    def test_active_connection_retries_are_limited_and_never_fail_the_service(self, active_connection_class):
        active_connection_class.return_value.run.side_effect = self.unreadable

        self.mediator._active_connections_list_update(
            ["/active/1"], retry=virtual_devices.OBJECT_READ_MAX_RETRIES
        )

        self.mediator._event_loop.call_later.assert_not_called()
        self.assertFalse(self.mediator.failed)


class ModemLookupTest(unittest.TestCase):
    def test_modem_manager_failure_does_not_break_the_connection(self):
        active = virtual_devices.ActiveConnection.__new__(virtual_devices.ActiveConnection)
        active._bus = MagicMock()
        active._bus.get_object.side_effect = dbus.exceptions.DBusException("ServiceUnknown")

        self.assertIsNone(active._find_modem_dbus_path_by_device("ttyACM0"))

    def test_modem_is_looked_up_again_on_state_change(self):
        """ModemManager restarting when the connection was read: operator and signal must come later."""
        active = virtual_devices.ActiveConnection(MagicMock(), MagicMock(), "/active/1", MagicMock())
        active._read_device_dbus_properties = MagicMock(return_value={"Interface": "ttyUSB1"})
        active._read_modem_dbus_properties = MagicMock(return_value={"m3gpp-operator-name": "Op"})
        active._find_modem_dbus_path_by_device = MagicMock(side_effect=[None, "/modem/0"])

        active.update({"Type": "gsm", "State": 1, "Devices": ["/devices/1"]})
        active.update({"Type": "gsm", "Ip4Config": "/"})  # no state change: no lookup
        self.assertEqual(1, active._find_modem_dbus_path_by_device.call_count)
        active.update({"State": 2})

        self.assertEqual("/modem/0", active._modem_path)
        self.assertEqual("Op", active.state.operator_name)
        self.assertEqual(2, active._find_modem_dbus_path_by_device.call_count)


class NetworkManagerListingTest(unittest.TestCase):
    """
    The listings run on the GLib thread asynchronously: a call that times out in the event loop's
    thread corrupts the heap. No introspection call, a short timeout, results posted as events.
    """

    def setUp(self):
        self.mediator = make_mediator()
        self.method = self.mediator._bus.get_object.return_value.get_dbus_method

    def listed(self):
        return [event.kwargs for event in posted_events(self.mediator)]

    @patch.object(virtual_devices.GLib, "idle_add")
    def test_listing_is_requested_on_the_glib_thread(self, idle_add):
        virtual_devices.ConnectionsMediator._request_network_manager_listing(self.mediator, OWNER, True)

        idle_add.assert_called_once_with(self.mediator._list_network_manager_objects, OWNER, True, 0)

    def test_restore_lists_connections_then_active_connections(self):
        self.assertFalse(self.mediator._list_network_manager_objects(OWNER, True, 0))
        self.method.assert_called_once_with("ListConnections", "org.freedesktop.NetworkManager.Settings")
        self.assertFalse(self.mediator._bus.get_object.call_args.kwargs["introspect"])
        kwargs = self.method.return_value.call_args.kwargs
        self.assertEqual(virtual_devices.NETWORK_MANAGER_LIST_TIMEOUT, kwargs["timeout"])

        kwargs["reply_handler"](["/settings/1"])
        self.method.assert_called_with("Get", "org.freedesktop.DBus.Properties")
        args = self.method.return_value.call_args.args
        self.assertEqual((virtual_devices.NETWORK_MANAGER_DBUS_NAME, "ActiveConnections"), args)
        self.method.return_value.call_args.kwargs["reply_handler"](["/active/1"])

        base = {"owner": OWNER, "restore": True, "retry": 0}
        self.assertEqual(
            [{**base, "connections": ["/settings/1"]}, {**base, "active_connections": ["/active/1"]}],
            self.listed(),
        )

    def test_reload_lists_only_active_connections(self):
        self.mediator._list_network_manager_objects(OWNER, False, 2)

        self.method.assert_called_once_with("Get", "org.freedesktop.DBus.Properties")

    def test_errors_are_posted(self):
        error = dbus.exceptions.DBusException("NoReply")
        self.mediator._list_network_manager_objects(OWNER, True, 0)
        self.method.return_value.call_args.kwargs["error_handler"](error)

        self.mediator._bus.get_object.side_effect = error
        self.mediator._list_network_manager_objects(OWNER, False, 1)

        self.assertEqual(
            [
                {"owner": OWNER, "restore": True, "retry": 0, "error": error},
                {"owner": OWNER, "restore": False, "retry": 1, "error": error},
            ],
            self.listed(),
        )

    @patch.object(virtual_devices.ConnectionsMediator, "_network_manager_listed")
    def test_listed_event_is_dispatched(self, listed):
        event = virtual_devices.Event(
            virtual_devices.EventType.NETWORK_MANAGER_LISTED,
            owner=OWNER,
            restore=True,
            retry=0,
            connections=[],
        )

        asyncio.run(self.mediator._run_async_event(event))

        listed.assert_called_once_with(owner=OWNER, restore=True, retry=0, connections=[])


class ActiveConnectionSubscriptionTest(unittest.TestCase):
    """A connection activated while it is being read must not stay 'activating'."""

    def setUp(self):
        self.active = virtual_devices.ActiveConnection(MagicMock(), MagicMock(), "/active/1", MagicMock())
        self.calls = MagicMock()
        self.active._active_connection_properties_changed_subscription = self.calls.properties
        self.active._ipv4_config_changed_subscription = self.calls.ipv4
        self.active._read_connection_dbus_properties = self.calls.read
        self.active._read_ipv4_dbus_properties = self.calls.read_ipv4
        self.calls.read.return_value = {"State": 1, "Connection": "/settings/1"}
        self.calls.read_ipv4.return_value = {}

    def names(self):
        return [name for name, _, _ in self.calls.mock_calls]

    def test_subscribes_before_reading(self):
        self.active.run()

        self.assertLess(self.names().index("properties.subscribe"), self.names().index("read"))

    def test_unreadable_connection_is_unsubscribed(self):
        self.calls.read.side_effect = dbus.exceptions.DBusException("UnknownObject")

        with self.assertRaises(dbus.exceptions.DBusException), self.assertNoLogs(level="INFO"):
            self.active.run()  # it was never created: no "Remove active connection" either

        self.calls.properties.unsubscribe.assert_called_with()
        self.calls.ipv4.unsubscribe.assert_called_with()

    def test_subscribes_to_ip4config_before_reading_addresses(self):
        self.active.update({"State": 2, "Ip4Config": "/ip4/1"})

        self.calls.ipv4.subscribe.assert_called_once_with("/ip4/1", active_connection_path="/active/1")
        self.assertLess(self.names().index("ipv4.subscribe"), self.names().index("read_ipv4"))


class ConnectivityTest(unittest.TestCase):
    def setUp(self):
        self.mediator = make_mediator()

    def test_stale_connectivity_is_dropped(self):
        active = make_active("/settings/1")
        self.mediator._active_connections["/active/1"] = active

        self.mediator._active_connection_connectivity_updated("/active/1", False, generation=0)

        active.update_connectivity.assert_not_called()

    def test_current_connectivity_is_applied(self):
        active = make_active("/settings/1")
        self.mediator._active_connections["/active/1"] = active

        self.mediator._active_connection_connectivity_updated("/active/1", True, generation=1)

        active.update_connectivity.assert_called_once_with(True)

    def test_stopped_check_is_forgotten(self):
        updater = virtual_devices.ConnectivityUpdater.__new__(virtual_devices.ConnectivityUpdater)
        future = MagicMock()
        updater._futures = {"/active/1": future}

        updater.stop_updates("/active/1")

        future.cancel.assert_called_once_with()
        self.assertEqual({}, updater._futures)

    def test_reset_cancels_checks_and_marks_results_stale(self):
        updater = virtual_devices.ConnectivityUpdater.__new__(virtual_devices.ConnectivityUpdater)
        future = MagicMock()
        updater._futures = {"/active/1": future}
        updater.generation = 0

        updater.reset()

        future.cancel.assert_called_once_with()
        self.assertEqual({}, updater._futures)
        self.assertEqual(1, updater.generation)


class CommonConnectionCreateTest(unittest.TestCase):
    def setUp(self):
        self.mediator = make_mediator()

    @patch.object(virtual_devices, "CommonConnection")
    def test_existing_connection_is_not_recreated(self, common_connection_class):
        existing = MagicMock()
        self.mediator._common_connections["/settings/1"] = existing

        self.mediator._common_connection_create("/settings/1")

        common_connection_class.assert_not_called()
        existing.stop.assert_not_called()
        self.assertIs(existing, self.mediator._common_connections["/settings/1"])

    @patch.object(virtual_devices, "CommonConnection")
    def test_late_connection_gets_active_state(self, common_connection_class):
        active = make_active("/settings/1")
        self.mediator._active_connections["/active/1"] = active
        self.mediator._active_connections["/active/2"] = make_active("/settings/2")

        self.mediator._common_connection_create("/settings/1")

        common_connection_class.return_value.update.assert_called_once_with(active.state)

    @patch.object(virtual_devices, "CommonConnection")
    def test_reactivated_connection_shows_new_active_connection(self, common_connection_class):
        activating = make_active("/settings/1", virtual_devices.ConnectionState.ACTIVATING)
        self.mediator._active_connections["/active/5"] = activating
        self.mediator._active_connections["/active/4"] = make_active(
            "/settings/1", virtual_devices.ConnectionState.DEACTIVATING
        )

        self.mediator._common_connection_create("/settings/1")

        common_connection_class.return_value.update.assert_called_once_with(activating.state)


class ActiveConnectionRemoveTest(unittest.TestCase):
    def setUp(self):
        self.mediator = make_mediator()
        self.common = MagicMock()
        self.mediator._common_connections["/settings/1"] = self.common

    def test_reactivated_connection_keeps_new_state(self):
        old = make_active("/settings/1", virtual_devices.ConnectionState.DEACTIVATING)
        new = make_active("/settings/1", virtual_devices.ConnectionState.ACTIVATING)
        self.mediator._active_connections = {"/active/4": old, "/active/5": new}

        self.mediator._active_connections_list_update(["/active/5"])

        old.stop.assert_called_once_with()
        self.common.update.assert_called_once_with(new.state)
        self.assertEqual({"/active/5": new}, self.mediator._active_connections)

    def test_deactivated_connection_gets_old_state(self):
        old = make_active("/settings/1", virtual_devices.ConnectionState.DEACTIVATING)
        self.mediator._active_connections = {"/active/4": old}

        self.mediator._active_connections_list_update([])

        self.common.update.assert_called_once_with(old.state)


class DeviceRemoveTest(unittest.TestCase):
    def setUp(self):
        self.mqtt_client = MagicMock()
        self.device = wbmqtt.Device(self.mqtt_client, "system__networks__uuid", "Net", "wb-nm-helper")
        self.device.create_control("UpDown", wbmqtt.ControlMeta(), "")
        self.device.create_control("Name", wbmqtt.ControlMeta(), "")

    def test_removed_device_drops_command_callbacks(self):
        self.device.add_control_message_callback("UpDown", MagicMock())

        self.device.remove_device()

        topic = "/devices/system__networks__uuid/controls/UpDown/on"
        self.mqtt_client.unsubscribe.assert_called_once_with(topic)
        self.mqtt_client.message_callback_remove.assert_called_once_with(topic)
