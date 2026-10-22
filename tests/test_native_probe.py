import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cast_to_tv.native_probe import APP_ID, NAMESPACE, StreamingController, run_probe, validate_event


class NativeBridgeTests(unittest.TestCase):
    def test_rejects_wrong_protocol_and_unexpected_control_routes(self) -> None:
        good = {"version": 1, "event": "message", "destination": "transport-1",
                "namespace": NAMESPACE, "payload": {"type": "OFFER"}}
        validate_event(good, "transport-1")
        for changed in ({"version": 2}, {"destination": "receiver-0"},
                        {"namespace": "other"}, {"payload": "string"}, {"event": "other"}):
            with self.assertRaises(RuntimeError):
                validate_event({**good, **changed}, "transport-1")

    def test_control_queue_overflow_reports_failure_without_blocking_socket_thread(self) -> None:
        controller = StreamingController()
        message = SimpleNamespace(source_id="receiver", namespace=NAMESPACE)
        for _ in range(257):
            controller.receive_message(message, {"type": "ANSWER"})
        self.assertTrue(controller.failed.is_set())
        self.assertEqual(controller.messages.qsize(), 256)

    def test_cancellation_during_receiver_launch_cleans_up_without_helper(self) -> None:
        device = Mock()
        device.app_id = None
        cancelled = threading.Event()
        device.register_handler.side_effect = lambda controller: controller.registered(device.socket_client)
        def cancel_launch(*args, **kwargs):
            device.app_id = APP_ID
            cancelled.set()
        device.socket_client.receiver_controller.launch_app.side_effect = cancel_launch
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            with patch("cast_to_tv.native_probe.subprocess.Popen") as launch_helper:
                run_probe(device, Path("helper.exe"), Path("video.ivf"), 200,
                          queue.Queue(), cancelled, report)
            launch_helper.assert_not_called()
            device.quit_app.assert_called_once()
            device.socket_client.unregister_handler.assert_called_once()
            self.assertTrue(report.is_file())

    def test_existing_namespace_does_not_require_a_new_channel_callback(self) -> None:
        device = Mock()
        device.app_id = None
        device.socket_client.app_namespaces = {NAMESPACE}
        device.socket_client.destination_id = "transport-1"
        device.socket_client.source_id = "sender-0"
        device.cast_info.host = "192.0.2.1"
        device.register_handler.side_effect = lambda controller: controller.registered(device.socket_client)

        def launch_app(*args, **kwargs):
            device.app_id = APP_ID
            kwargs["callback_function"](True, {"type": "RECEIVER_STATUS"})

        device.socket_client.receiver_controller.launch_app.side_effect = launch_app
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            with patch("cast_to_tv.native_probe.subprocess.Popen", side_effect=RuntimeError("missing helper")) as helper:
                with self.assertRaisesRegex(RuntimeError, "missing helper"):
                    run_probe(device, Path("helper.exe"), Path("video.ivf"), 200,
                              queue.Queue(), threading.Event(), report)
            helper.assert_called_once()
            device.quit_app.assert_called_once()
            self.assertIn("missing helper", report.read_text())


if __name__ == "__main__":
    unittest.main()
