import unittest
from unittest.mock import Mock, call, patch

from cast_to_tv.power import (
    ES_CONTINUOUS, ES_DISPLAY_REQUIRED, ES_SYSTEM_REQUIRED, ScreenAwake,
)


class ScreenAwakeTests(unittest.TestCase):
    @patch("cast_to_tv.power.ctypes.WinDLL")
    def test_requests_display_and_system_then_releases_without_changing_power_plan(self, dll: Mock) -> None:
        set_state = dll.return_value.SetThreadExecutionState
        set_state.return_value = ES_CONTINUOUS
        awake = ScreenAwake()

        awake.set_enabled(False)
        dll.assert_not_called()
        awake.set_enabled(True)
        awake.set_enabled(True)
        awake.set_enabled(False)
        awake.set_enabled(False)

        self.assertEqual(set_state.call_args_list, [
            call(ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED),
            call(ES_CONTINUOUS),
        ])

    @patch("cast_to_tv.power.ctypes.WinDLL")
    def test_failed_request_is_reported_and_can_be_retried(self, dll: Mock) -> None:
        set_state = dll.return_value.SetThreadExecutionState
        set_state.side_effect = [0, ES_CONTINUOUS, 0, ES_CONTINUOUS]
        awake = ScreenAwake()

        with self.assertRaises(OSError):
            awake.set_enabled(True)
        awake.set_enabled(True)
        with self.assertRaises(OSError):
            awake.set_enabled(False)
        awake.set_enabled(False)

        self.assertEqual(set_state.call_count, 4)


if __name__ == "__main__":
    unittest.main()
