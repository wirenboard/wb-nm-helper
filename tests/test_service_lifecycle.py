import asyncio
import json
import logging
import signal
from concurrent.futures import Future
from unittest.mock import MagicMock, call, patch

import pytest

from wb.nm_helper import virtual_devices, wbmqtt

# Lifecycle tests intentionally exercise callbacks and state owned by the service.
# pylint: disable=protected-access


def test_missing_config_returns_6():
    with patch.object(
        virtual_devices,
        "load_connectivity_config_file",
        side_effect=FileNotFoundError("missing"),
    ):
        assert virtual_devices.main(["-c", "/tmp/missing.conf"]) == virtual_devices.EXIT_NOT_CONFIGURED


@pytest.mark.parametrize(
    "url",
    ["invalid://broker", "tcp://localhost", "tcp://:1883", "unix://"],
)
def test_invalid_broker_is_invalid_argument(url):
    with pytest.raises(SystemExit) as error:
        virtual_devices.main(["--broker", url])
    assert error.value.code == virtual_devices.EXIT_INVALID_ARGUMENT


def test_signal_requests_exit_7_and_cleanup():
    handlers = {}
    mqtt_client = MagicMock()
    mqtt_client.is_connected.return_value = True
    mediator = MagicMock()
    mediator.run.return_value = virtual_devices.EXIT_STOPPED
    publication = MagicMock()
    publication.is_published.return_value = True
    mediator.stop.return_value = [publication]

    def register(signum, handler):
        handlers[signum] = handler

    def stop_during_start():
        handlers[signal.SIGTERM](signal.SIGTERM, None)

    mqtt_client.start.side_effect = stop_during_start
    with patch.object(
        virtual_devices, "load_connectivity_config_file", return_value=MagicMock()
    ), patch.object(virtual_devices, "MQTTClient", return_value=mqtt_client), patch.object(
        virtual_devices, "ConnectionsMediator", return_value=mediator
    ), patch.object(
        virtual_devices.wbmqtt, "remove_topics_by_device_prefix"
    ), patch.object(
        virtual_devices.signal, "signal", side_effect=register
    ):
        assert virtual_devices.main([]) == virtual_devices.EXIT_STOPPED

    mediator.request_stop.assert_called_once_with(virtual_devices.EXIT_STOPPED)
    mediator.stop.assert_called_once_with(remove_devices=True)
    publication.wait_for_publish.assert_called_once()
    mqtt_client.stop.assert_called_once_with()


def test_runtime_failure_returns_1_without_device_cleanup():
    mqtt_client = MagicMock()
    mqtt_client.is_connected.return_value = True
    mediator = MagicMock()
    mediator.run.side_effect = RuntimeError("boom")
    with patch.object(
        virtual_devices, "load_connectivity_config_file", return_value=MagicMock()
    ), patch.object(virtual_devices, "MQTTClient", return_value=mqtt_client), patch.object(
        virtual_devices, "ConnectionsMediator", return_value=mediator
    ), patch.object(
        virtual_devices.wbmqtt, "remove_topics_by_device_prefix"
    ), patch.object(
        virtual_devices.signal, "signal"
    ):
        assert virtual_devices.main([]) == virtual_devices.EXIT_FAILURE

    mediator.stop.assert_called_once_with(remove_devices=False)
    mqtt_client.stop.assert_called_once_with()


def test_mosquitto_authentication_refusal_requests_exit_2():
    mediator = MagicMock()
    monitor = virtual_devices.MosquittoMonitor(mediator, MagicMock())

    monitor._on_connect(None, None, None, 5)

    mediator.request_stop.assert_called_once_with(virtual_devices.EXIT_INVALID_ARGUMENT)


def test_other_connack_refusal_retries_and_republishes():
    mediator = MagicMock()
    monitor = virtual_devices.MosquittoMonitor(mediator, MagicMock())

    monitor._on_connect(None, None, None, 3)
    monitor._on_connect(None, None, None, 0)

    mediator.request_stop.assert_not_called()
    event = mediator.new_event.call_args.args[0]
    assert event.type == virtual_devices.EventType.RELOAD_CONNECTIONS


def test_mosquitto_reconnect_requests_full_republish():
    mediator = MagicMock()
    monitor = virtual_devices.MosquittoMonitor(mediator, MagicMock())
    monitor._on_disconnect(None, None, 1)
    monitor._on_connect(None, None, None, 0)

    event = mediator.new_event.call_args.args[0]
    assert event.type == virtual_devices.EventType.RELOAD_CONNECTIONS


def test_async_event_failure_stops_main_loop():
    mediator = object.__new__(virtual_devices.ConnectionsMediator)
    mediator._exit_code = None
    mediator._dbus_loop = MagicMock()
    future = Future()
    future.set_exception(RuntimeError("boom"))

    def submit(coroutine):
        coroutine.close()
        return future

    mediator._event_loop = MagicMock()
    mediator._event_loop.run_coroutine_threadsafe.side_effect = submit
    with patch.object(virtual_devices.GLib, "idle_add") as idle_add:
        mediator.new_event(virtual_devices.Event(virtual_devices.EventType.RELOAD_CONNECTIONS))

    assert mediator._exit_code == virtual_devices.EXIT_FAILURE
    idle_add.assert_called_once_with(mediator._dbus_loop.quit)


def test_reload_only_restarts_connectivity_checks():
    mediator = object.__new__(virtual_devices.ConnectionsMediator)
    mediator._connectivity_updater = MagicMock()
    mediator._active_connections = {
        "/active/1": MagicMock(),
        "/active/2": MagicMock(),
    }

    mediator._reload_connectivity()

    assert mediator._connectivity_updater.update.call_args_list == [
        call("/active/1", virtual_devices.CONNECTIVITY_CHECK_PERIOD),
        call("/active/2", virtual_devices.CONNECTIVITY_CHECK_PERIOD),
    ]


def test_invalid_connectivity_config_is_reported_without_stopping(caplog):
    updater = object.__new__(virtual_devices.ConnectivityUpdater)
    updater._bus = MagicMock()
    updater._connection_checker = MagicMock()
    updater._config_path = "/tmp/invalid.conf"
    updater._mediator = MagicMock()

    with patch.object(virtual_devices, "NMActiveConnection"), patch.object(
        virtual_devices,
        "load_connectivity_config_file",
        side_effect=json.JSONDecodeError("invalid", "{", 1),
    ), caplog.at_level(logging.ERROR):
        asyncio.run(updater._check_connectivity("/active/1", None))

    event = updater._mediator.new_event.call_args.args[0]
    assert event.type == virtual_devices.EventType.ACTIVE_CONNECTIVITY_UPDATED
    assert event.kwargs["connectivity"] is False
    assert "Unable to read connectivity for /active/1" in caplog.messages[0]


def test_disconnected_cleanup_is_reported(caplog):
    mqtt_client = MagicMock()
    mqtt_client.is_connected.return_value = False
    mediator = MagicMock()
    mediator.run.return_value = virtual_devices.EXIT_STOPPED
    with patch.object(
        virtual_devices, "load_connectivity_config_file", return_value=MagicMock()
    ), patch.object(virtual_devices, "MQTTClient", return_value=mqtt_client), patch.object(
        virtual_devices, "ConnectionsMediator", return_value=mediator
    ), patch.object(
        virtual_devices.wbmqtt, "remove_topics_by_device_prefix"
    ), patch.object(
        virtual_devices.signal, "signal"
    ), caplog.at_level(
        logging.ERROR
    ):
        assert virtual_devices.main([]) == virtual_devices.EXIT_STOPPED

    assert "Unable to remove virtual devices: MQTT broker is unavailable" in caplog.messages


def test_wait_for_publications_reports_timeout():
    published = MagicMock()
    published.is_published.return_value = True
    timed_out = MagicMock()
    timed_out.is_published.return_value = False

    assert wbmqtt.wait_for_publications([published, timed_out], timeout=0.1) == 1
