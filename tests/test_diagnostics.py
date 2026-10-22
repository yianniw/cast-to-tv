import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cast_to_tv.diagnostics import LatencyMarker, SessionDiagnostics, build_report, manifest_tracks


class DiagnosticReportTests(unittest.TestCase):
    def test_media_progress_is_not_reported_as_visible_latency(self) -> None:
        events = [
            {"event": "capture_started", "elapsed_seconds": 1},
            {"event": "receiver_status", "elapsed_seconds": 12, "state": "PLAYING",
             "manifest_end_minus_playhead_seconds": 10},
            {"event": "encoder_progress", "elapsed_seconds": 12, "values": {"out_time_us": "10800000"}},
        ]
        report = build_report(events)
        self.assertIsNone(report["visible_latency_seconds"])
        self.assertEqual(report["visible_latency_method"], "not measured")
        self.assertEqual(report["manifest_end_minus_playhead_seconds"]["median"], 10)
        self.assertAlmostEqual(report["output_timestamp_vs_capture_elapsed_gap_seconds"]["median"], .2)

    def test_reports_actual_simultaneous_clock_samples_and_percentiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            samples = Path(directory) / "samples.csv"
            samples.write_text("pc_seconds,tv_seconds\n20,19\n30,27\n40,38\n", encoding="utf-8")
            report = build_report([], samples)
            self.assertEqual(report["visible_latency_seconds"]["count"], 3)
            self.assertEqual(report["visible_latency_seconds"]["median"], 2)
            self.assertEqual(report["visible_latency_seconds"]["p95"], 3)
            samples.write_text("pc_seconds,tv_seconds\n20,21\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_report([], samples)

    def test_expands_repeats_timescales_and_discontinuities_in_a_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "desktop.mpd"
            manifest.write_text(
                '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period><AdaptationSet>'
                '<Representation id="0" mimeType="video/mp4"><SegmentTemplate '
                'timescale="1000" startNumber="5" media="chunk-stream$RepresentationID$-$Number%05d$.m4s">'
                '<SegmentTimeline><S t="4000" d="2000" r="1"/><S t="10000" d="1000"/>'
                '</SegmentTimeline></SegmentTemplate></Representation></AdaptationSet></Period></MPD>',
                encoding="utf-8",
            )
            track = manifest_tracks(manifest)[0]
            self.assertEqual(track["end_seconds"], 11)
            self.assertEqual([segment["start_seconds"] for segment in track["segments"]], [4, 6, 10])
            self.assertEqual(track["segments"][-1]["file"], "chunk-stream0-00007.m4s")

    def test_listener_is_reused_and_detached_across_sessions(self) -> None:
        controller = Mock()
        with tempfile.TemporaryDirectory() as directory:
            first = SessionDiagnostics(Path(directory))
            first.attach(controller)
            listener = controller._cast_to_tv_diagnostics_listener
            first.record("capture_started")
            first.close()
            self.assertIsNone(listener.diagnostics)
            second = SessionDiagnostics(Path(directory))
            second.attach(controller)
            self.assertIs(listener.diagnostics, second)
            controller.register_status_listener.assert_called_once()
            second.close()
            self.assertIsNone(listener.diagnostics)
            summary = json.loads((first.directory / "summary.json").read_text())
            self.assertIn("capture_started", summary["milestones_seconds"])
            self.assertIsNone(summary["visible_latency_seconds"])


class LatencyMarkerTests(unittest.TestCase):
    def marker(self, diagnostics=None):
        marker = LatencyMarker.__new__(LatencyMarker)
        marker.diagnostics = diagnostics
        marker.started = 100
        marker.label = Mock()
        marker.window = Mock()
        marker.audio = Mock(get=Mock(return_value=False))
        marker._last_flash = -1
        marker._after = None
        return marker

    def test_clock_updates_and_closes_without_a_recorder(self):
        marker = self.marker()
        with patch("cast_to_tv.diagnostics.time.monotonic", return_value=105.1):
            marker._tick()
        self.assertIn("000005.1 s", marker.label.configure.call_args.kwargs["text"])
        self.assertIn("flash 0001", marker.label.configure.call_args.kwargs["text"])
        marker.window.after.assert_called_once_with(100, marker._tick)
        marker.close()
        marker.window.after_cancel.assert_called_once_with(marker._after)
        marker.window.destroy.assert_called_once()

    def test_optional_audio_tick_works_without_a_recorder(self):
        marker = self.marker()
        marker.audio.get.return_value = True
        with patch("cast_to_tv.diagnostics.time.monotonic", return_value=105.1), \
                patch("cast_to_tv.diagnostics.os.name", "nt"), \
                patch("cast_to_tv.diagnostics.threading.Thread") as thread, \
                patch("winsound.Beep") as beep:
            marker._tick()
            thread.assert_called_once_with(target=marker._beep, daemon=True)
            marker._beep()
            beep.assert_called_once_with(880, 60)

    def test_explicit_developer_recorder_receives_marker_events(self):
        diagnostics = Mock()
        marker = self.marker(diagnostics)
        with patch("cast_to_tv.diagnostics.time.monotonic", return_value=105.1), \
                patch("winsound.Beep"):
            marker._tick()
            marker._beep()
        self.assertEqual(diagnostics.record.call_args_list[0].args, ("marker_flash",))
        self.assertAlmostEqual(diagnostics.record.call_args_list[0].kwargs["clock_seconds"], 5.1)
        diagnostics.record.assert_called_with("marker_audio_tick")


if __name__ == "__main__":
    unittest.main()
