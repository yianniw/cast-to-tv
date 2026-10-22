import unittest

from cast_to_tv.displays import list_display_ids


class DisplayIdentityTests(unittest.TestCase):
    def test_identity_survives_reordered_monitors_and_changed_geometry(self) -> None:
        original = [
            {"unique_id": "Monitor-A", "left": 0, "width": 1920},
            {"unique_id": "Monitor-B", "left": 1920, "width": 1920},
        ]
        changed = [
            {"unique_id": "Monitor-B", "left": -2560, "width": 2560},
            {"unique_id": "MONITOR-A", "left": 0, "width": 1280},
        ]
        self.assertEqual(list_display_ids(original), ["monitor-a", "monitor-b"])
        self.assertEqual(list_display_ids(changed), ["monitor-b", "monitor-a"])

    def test_missing_or_duplicate_identities_never_fall_back_to_geometry(self) -> None:
        displays = [
            {"unique_id": "monitor-a", "left": 0},
            {"unique_id": "MONITOR-A", "left": 1920},
            {"left": 0},
            {"unique_id": ""},
            {"unique_id": 123},
            {"unique_id": "monitor-b"},
        ]
        self.assertEqual(list_display_ids(displays), [None, None, None, None, None, "monitor-b"])


if __name__ == "__main__":
    unittest.main()
