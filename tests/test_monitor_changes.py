import unittest
from dataclasses import replace
from unittest.mock import MagicMock, Mock, patch

import test_display_switching as switching
from cast_to_tv.recovery import ReceiverCleanupSession, ReceiverOwnership, RecoveryError, failure_message


class MonitorChangesTests(unittest.TestCase):
    def setUp(self):
        self.fixture = switching.DisplaySwitchingTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.app = self.fixture.app
        self.app._reset_recovery()
        self.app._display_refresh_error = None
        self.app._display_stop_reason = None
        self.app._display_timer = None
        self.app.displays = [dict(display, unique_id=identity) for display, identity in
                             zip(self.app.displays, self.app._display_ids)]
        self.original = [dict(display) for display in self.app.displays]
        self.snapshot = list(self.original)
        self.fixture.enumerate_displays.side_effect = lambda: [dict(display) for display in self.snapshot]
        self.app._active_request = replace(self.app._active_request, display=dict(self.original[0]))
        self.request = self.app._active_request
        self.app.display_box = MagicMock()
        self.app.display_box.current.side_effect = self.fixture.current
        self.app.display_box.set.side_effect = lambda value: self.fixture.current(-1)
        self.status = "Low-delay mirroring Display 1 to the TV. Video only."
        self.app.status.get.side_effect = lambda: self.status
        self.app.status.set.side_effect = lambda value: setattr(self, "status", value)

    def idle(self):
        self.app.session = None
        self.app._active_request = None
        self.app.busy = False

    def refresh(self, displays):
        self.snapshot = displays
        return self.app._refresh_displays()

    def test_reorder_keeps_selected_and_active_identity_without_restarting(self):
        self.refresh([self.original[2], self.original[0], self.original[1]])
        self.assertEqual(self.fixture.current(), 1)
        self.assertEqual(self.app._active_request.display_index, 1)
        self.assertEqual(self.app._active_request.display, self.request.display)
        self.assertIn("Display 2", self.status)
        self.fixture.thread.assert_not_called()
        self.fixture.save.assert_not_called()
        self.app._display_identifiers.close.assert_called_once()

    def test_disconnect_reconnect_restores_saved_choice_without_starting(self):
        self.idle()
        self.refresh(self.original[1:])
        self.assertEqual(self.fixture.current(), -1)
        self.app.start_button.configure.assert_called_with(state="disabled")
        self.assertIn("Saved display is unavailable", self.app.status.set.call_args.args[0])
        self.assertEqual(self.app._saved_display_id, "monitor-a")
        self.refresh([self.original[1], self.original[0]])
        self.assertEqual(self.fixture.current(), 1)
        self.app.start_button.configure.assert_called_with(state="normal")
        self.fixture.save.assert_not_called()
        self.fixture.thread.assert_not_called()

    def test_replacement_at_same_rectangle_is_not_selected(self):
        self.idle()
        self.refresh([dict(self.original[0], unique_id="different-monitor"), *self.original[1:]])
        self.assertEqual(self.fixture.current(), -1)

    def test_ambiguous_identity_clears_selection_and_stops_capture(self):
        self.refresh([self.original[0], dict(self.original[1], unique_id="MONITOR-A")])
        self.assertEqual(self.fixture.current(), -1)
        self.assertTrue(self.app._stopping)
        self.fixture.run_worker()
        self.assertIsNone(self.app.session)

    def test_unidentified_selection_survives_only_an_unchanged_snapshot(self):
        self.idle()
        self.app.displays = [{key: value for key, value in display.items() if key != "unique_id"}
                             for display in self.original]
        self.app._saved_display_id = None
        self.refresh(list(self.app.displays))
        self.assertEqual(self.fixture.current(), 0)
        self.refresh([*self.app.displays, dict(self.original[2], unique_id="new-monitor")])
        self.assertEqual(self.fixture.current(), -1)

    def test_unrelated_monitor_change_does_not_stop_capture(self):
        self.refresh([self.original[0], self.original[2]])
        self.fixture.thread.assert_not_called()
        self.assertIs(self.app.session, self.fixture.old)

    def test_display_loss_stops_once_in_both_modes_and_never_restarts(self):
        for native in (False, True):
            with self.subTest(native=native):
                self.app.displays = list(self.original)
                self.app._active_request = replace(self.request, native=native)
                self.app.session = self.fixture.old
                self.app.busy = False
                self.app._display_stop_reason = None
                self.fixture.thread.reset_mock()
                self.refresh(self.original[1:])
                self.app._poll_displays()
                self.fixture.thread.assert_called_once()
                self.fixture.run_worker()
                self.assertIsNone(self.app.session)
                self.assertIsNone(self.app._pending_switch)
                self.assertIsNone(self.app._recovery_request)
                self.assertIn("cast display disconnected", self.app.status.set.call_args.args[0])
                self.app._screen_awake.set_enabled.assert_called_with(False)
                self.refresh(self.original)
                self.assertIsNone(self.app.session)
                self.fixture.thread.assert_called_once()
        self.fixture.save.assert_not_called()

    def test_each_capture_coordinate_change_stops_both_modes(self):
        for native in (False, True):
            for key in ("left", "top", "width", "height"):
                with self.subTest(native=native, key=key):
                    self.app.displays = list(self.original)
                    self.app._active_request = replace(self.request, native=native)
                    self.app.session = self.fixture.old
                    self.app.busy = False
                    self.refresh([dict(self.original[0], **{key: self.original[0][key] + 1}),
                                  *self.original[1:]])
                    self.assertTrue(self.app._stopping)
                    self.assertEqual(self.app._active_request.display, self.original[0])
                    self.fixture.run_worker()
                    self.assertIsNone(self.app.session)

    def test_loss_during_startup_uses_cancel_then_waits_for_worker(self):
        self.app.busy = True
        self.refresh(self.original[1:])
        self.fixture.old.cancel_start.assert_called_once()
        self.fixture.thread.assert_not_called()
        self.app._post_session_event(self.fixture.old, "started", None)
        self.app._process_events()
        self.fixture.run_worker()
        self.assertIsNone(self.app.session)
        self.assertEqual(len(switching.FakeNativeSession.instances), 1)

    def test_disappearing_switch_target_cancels_pending_restart(self):
        self.fixture.select(1)
        self.refresh([self.original[0], self.original[2]])
        self.assertIsNone(self.app._pending_switch)
        self.fixture.thread.assert_called_once()
        self.fixture.run_worker()
        self.assertIsNone(self.app.session)
        self.assertEqual(len(switching.FakeNativeSession.instances), 1)

    def test_reorder_during_switch_keeps_target_identity(self):
        self.fixture.select(1)
        self.refresh([self.original[2], self.original[0], self.original[1]])
        self.assertEqual(self.app._pending_switch.display_index, 2)
        self.fixture.run_worker()
        self.assertEqual(self.app._active_request.display_index, 2)
        self.assertEqual(self.app.session.display, self.original[1])
        self.fixture.run_worker()

    def test_loss_during_failure_cleanup_cancels_automatic_recovery(self):
        self.app._post_session_event(self.app.session, "capture_error",
                                    failure_message(RecoveryError("Lost connection", "connection")))
        self.app._process_events()
        self.assertTrue(self.app._recovery_cleanup)
        self.refresh([])
        self.assertTrue(self.app._cancel_recovery)
        self.fixture.run_worker()
        self.assertIsNone(self.app.session)
        self.assertIsNone(self.app._recovery_request)
        self.assertEqual(len(switching.FakeNativeSession.instances), 1)

    def test_invalid_recovery_offer_releases_owned_receiver(self):
        self.idle()
        self.app._recovery_request = replace(self.request, recovery_receiver=ReceiverOwnership("app", "route"))
        self.refresh([])
        self.assertIsInstance(self.app.session, ReceiverCleanupSession)
        with patch.object(self.app.session, "stop", return_value=[]) as stop:
            self.fixture.run_worker()
        stop.assert_called_once()
        self.assertIsNone(self.app.session)
        self.assertIsNone(self.app._recovery_request)

    def test_enumeration_failure_retains_choices_disables_start_and_recovers(self):
        self.idle()
        self.fixture.enumerate_displays.side_effect = OSError("enumeration unavailable")
        with self.assertLogs("cast_to_tv.app", level="WARNING") as logs:
            self.assertFalse(self.app._refresh_displays())
            self.assertFalse(self.app._refresh_displays())
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(self.app.displays, self.original)
        self.app.start_button.configure.assert_called_with(state="disabled")
        self.app.identify_button.configure.assert_called_with(state="disabled")
        self.fixture.enumerate_displays.side_effect = lambda: list(self.original)
        self.app._refresh_displays()
        self.app.start_button.configure.assert_called_with(state="normal")
        self.app.identify_button.configure.assert_called_with(state="normal")
        self.fixture.error.assert_not_called()

    def test_enumeration_failure_stops_existing_capture(self):
        self.fixture.enumerate_displays.side_effect = OSError("enumeration unavailable")
        with self.assertLogs("cast_to_tv.app", level="WARNING"):
            self.app._refresh_displays()
        self.assertTrue(self.app._stopping)
        self.fixture.run_worker()
        self.assertIsNone(self.app.session)
        self.app.start_button.configure.assert_called_with(state="disabled")

    def test_preflight_blocks_start_if_selected_monitor_disappeared_between_polls(self):
        self.idle()
        self.snapshot = self.original[1:]
        self.app._show_error = Mock()
        self.app.start_cast()
        self.fixture.thread.assert_not_called()
        self.assertIsNone(self.app.session)

    def test_preflight_blocks_automatic_recovery_and_releases_receiver(self):
        self.idle()
        self.snapshot = []
        request = replace(self.request, recovery_receiver=ReceiverOwnership("app", "route"))
        self.app._begin_cast(request)
        self.assertIsInstance(self.app.session, ReceiverCleanupSession)
        with patch.object(self.app.session, "stop", return_value=[]):
            self.fixture.run_worker()
        self.assertIsNone(self.app.session)
        self.assertEqual(len(switching.FakeNativeSession.instances), 1)

    def test_cleanup_failure_with_missing_display_can_retry_stop(self):
        self.fixture.old.stop.return_value = ["Cleanup pending"]
        self.refresh([])
        self.fixture.run_worker()
        self.assertIs(self.app.session, self.fixture.old)
        self.assertEqual(self.app._active_request.display_index, -1)
        self.app.stop_button.configure.assert_called_with(state="normal")
        self.fixture.old.stop.return_value = []
        self.app.stop_cast()
        self.fixture.run_worker()
        self.assertIsNone(self.app.session)

    def test_reorder_after_failed_stop_preserves_cleanup_feedback(self):
        self.fixture.old.stop.return_value = ["Cleanup pending"]
        self.app.stop_cast()
        self.fixture.run_worker()
        self.refresh([self.original[2], self.original[0], self.original[1]])
        self.assertIn("Cleanup pending", self.status)
        self.assertIn("Choose Stop", self.status)

    def test_hidden_app_still_polls_and_restores_to_explain_monitor_loss(self):
        self.app._hidden_to_tray = True
        self.snapshot = self.original[1:]
        self.app._poll_displays()
        self.assertFalse(self.app._hidden_to_tray)
        self.app.root.deiconify.assert_called_once()
        self.assertTrue(self.app._stopping)

    def test_close_cancels_poll_timer_and_late_poll_does_not_enumerate(self):
        self.app._display_timer = "monitor-timer"
        self.app.close()
        self.app.root.after_cancel.assert_called_with("monitor-timer")
        self.fixture.enumerate_displays.reset_mock()
        self.app._poll_displays()
        self.fixture.enumerate_displays.assert_not_called()
        self.assertIsNone(self.app._display_timer)


if __name__ == "__main__":
    unittest.main()
