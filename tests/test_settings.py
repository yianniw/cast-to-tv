import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cast_to_tv.settings import DEFAULT_SETTINGS, load_settings, save_settings, settings_path


class SettingsTests(unittest.TestCase):
    def test_quality_choices_save_independently_and_preserve_other_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"include_audio": false, "display_id": "monitor-a"}', encoding="utf-8")
            self.assertEqual(load_settings(path)["resolution"], "1080p")
            self.assertEqual(load_settings(path)["frame_rate"], 30)
            for resolution in ("720p", "1080p"):
                save_settings({"resolution": resolution}, path)
                for rate in (15, 30):
                    save_settings({"frame_rate": rate}, path)
                    self.assertEqual(load_settings(path), {
                        **DEFAULT_SETTINGS, "resolution": resolution, "frame_rate": rate,
                        "include_audio": False, "display_id": "monitor-a",
                    })

    def test_invalid_quality_defaults_each_choice_without_losing_valid_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for name, values in (("resolution", (None, "4K", 720, [], {})),
                                 ("frame_rate", (None, True, 15.0, "30", 60, [], {}))):
                for value in values:
                    with self.subTest(name=name, value=value):
                        choices = {"resolution": "720p", "frame_rate": 15, "include_audio": False}
                        choices[name] = value
                        path.write_text(json.dumps(choices), encoding="utf-8")
                        with self.assertLogs("cast_to_tv.settings", level="WARNING"):
                            loaded = load_settings(path)
                        choices[name] = DEFAULT_SETTINGS[name]
                        self.assertEqual(loaded, {**DEFAULT_SETTINGS, **choices})
                        with self.assertRaises(ValueError):
                            save_settings({name: value}, path)

    def test_old_settings_default_to_normal_close_and_tray_choice_merges_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"include_audio": false, "keep_screen_on": true}', encoding="utf-8")
            original = load_settings(path)
            self.assertFalse(original["close_to_tray"])
            save_settings({"close_to_tray": True}, path)
            self.assertEqual(load_settings(path), {**original, "close_to_tray": True})

    def test_missing_settings_use_checkbox_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_settings(Path(directory) / "settings.json"), DEFAULT_SETTINGS)

    def test_both_choices_persist_between_loads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CastToTV" / "settings.json"
            for enabled in (True, False):
                choices = {name: enabled for name, value in DEFAULT_SETTINGS.items() if isinstance(value, bool)}
                save_settings(choices, path)
                self.assertEqual(load_settings(path), {**DEFAULT_SETTINGS, **choices})
                self.assertEqual(json.loads(path.read_text()), {**DEFAULT_SETTINGS, **choices})
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_invalid_settings_default_to_off(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for content in ('{broken', '[]', 'null', '{"keep_screen_on": "false"}',
                            '{"keep_screen_on": 1}', '{"keep_screen_on": null}'):
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with self.assertLogs("cast_to_tv.settings", level="WARNING"):
                        self.assertEqual(load_settings(path), DEFAULT_SETTINGS)

    def test_existing_screen_on_setting_is_preserved_when_new_settings_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"keep_screen_on": true}', encoding="utf-8")
            self.assertEqual(load_settings(path), {
                **DEFAULT_SETTINGS,
                "keep_screen_on": True, "include_audio": True, "measure_delay": False,
            })
            save_settings({"include_audio": False}, path)
            save_settings({"measure_delay": True}, path)
            self.assertEqual(load_settings(path), {
                **DEFAULT_SETTINGS,
                "keep_screen_on": True, "include_audio": False, "measure_delay": True,
            })

    def test_saving_one_checkbox_preserves_other_choices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings({"include_audio": False, "measure_delay": True}, path)
            save_settings({"keep_screen_on": True}, path)
            save_settings({"include_audio": True}, path)
            self.assertEqual(load_settings(path), {
                **DEFAULT_SETTINGS,
                "keep_screen_on": True, "include_audio": True, "measure_delay": True,
            })

    def test_invalid_checkbox_does_not_discard_other_saved_choices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for name, default in DEFAULT_SETTINGS.items():
                if not isinstance(default, bool):
                    continue
                with self.subTest(name=name):
                    choices = {key: not value if isinstance(value, bool) else value
                               for key, value in DEFAULT_SETTINGS.items()}
                    choices[name] = "invalid"
                    path.write_text(json.dumps(choices), encoding="utf-8")
                    with self.assertLogs("cast_to_tv.settings", level="WARNING"):
                        loaded = load_settings(path)
                    choices[name] = default
                    self.assertEqual(loaded, choices)

    def test_selection_and_checkbox_updates_preserve_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            tv_uuid = "11111111-2222-4333-8444-555555555555"
            save_settings({"include_audio": False}, path)
            save_settings({"tv_uuid": tv_uuid}, path)
            save_settings({"display_id": "monitor-a"}, path)
            save_settings({"measure_delay": True}, path)
            self.assertEqual(load_settings(path), {
                **DEFAULT_SETTINGS, "tv_uuid": tv_uuid, "display_id": "monitor-a",
                "include_audio": False, "measure_delay": True,
            })

    def test_invalid_selections_do_not_discard_checkbox_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for selections in ({"tv_uuid": "not-a-uuid", "display_id": 123},
                               {"tv_uuid": False, "display_id": ""}):
                with self.subTest(selections=selections):
                    path.write_text(json.dumps({"keep_screen_on": True, **selections}), encoding="utf-8")
                    with self.assertLogs("cast_to_tv.settings", level="WARNING"):
                        loaded = load_settings(path)
                    self.assertEqual(loaded, {**DEFAULT_SETTINGS, "keep_screen_on": True})

    def test_tv_uuid_case_is_normalized_and_selections_can_be_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings({"tv_uuid": "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE", "display_id": "monitor-a"}, path)
            self.assertEqual(load_settings(path)["tv_uuid"], "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
            save_settings({"tv_uuid": None, "display_id": None}, path)
            self.assertEqual(load_settings(path), DEFAULT_SETTINGS)

    def test_failed_replace_preserves_previous_preference_and_cleans_up(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings({"keep_screen_on": True, "include_audio": False, "measure_delay": True}, path)
            previous = load_settings(path)
            with patch("cast_to_tv.settings.os.replace", side_effect=OSError("file locked")):
                with self.assertRaises(OSError):
                    save_settings({"keep_screen_on": False}, path)
            self.assertEqual(load_settings(path), previous)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_default_path_is_beside_the_app(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("cast_to_tv.paths.application_directory", return_value=Path(directory)):
                self.assertEqual(settings_path(), Path(directory) / "settings.json")
                save_settings({"keep_screen_on": True})
                self.assertTrue(load_settings()["keep_screen_on"])


if __name__ == "__main__":
    unittest.main()
