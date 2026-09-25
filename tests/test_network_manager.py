from unittest import TestCase
from unittest.mock import MagicMock, call

from wb.nm_helper.network_manager import DbusObject


class DbusObjectTests(TestCase):
    def test_get_object_follows_name_owner_changes(self):
        # A proxy bound to a well-known name (e.g. org.freedesktop.NetworkManager) must track
        # its current owner: without follow_name_owner_changes it stays bound to whoever owned
        # the name when the proxy was first created, so a NetworkManager restart (as happens on
        # a package upgrade) leaves every later call hitting a dead, now-unowned unique name.
        bus = MagicMock()
        obj = DbusObject("/some/path", bus, "some.interface", "some.dbus.name")

        obj.get_object()

        self.assertEqual(
            [call("some.dbus.name", "/some/path", introspect=False, follow_name_owner_changes=True)],
            bus.get_object.mock_calls,
        )

    def test_get_object_is_cached(self):
        bus = MagicMock()
        obj = DbusObject("/some/path", bus, "some.interface", "some.dbus.name")

        first = obj.get_object()
        second = obj.get_object()

        self.assertIs(first, second)
        bus.get_object.assert_called_once()
