import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pychromecast.discovery import SimpleCastListener
from pychromecast.error import ChromecastConnectionError

from cast_to_tv.casting import CastingError, discover_cast_devices


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.zconf = Mock()
        self.browser = Mock(zc=self.zconf, devices={})
        self.browser_factory = self.patch(
            "cast_to_tv.casting.CastBrowser", return_value=self.browser,
        )
        self.patch("cast_to_tv.casting.zeroconf.Zeroconf", return_value=self.zconf)
        self.sleep = self.patch("cast_to_tv.casting.time.sleep")
        self.device_factory = self.patch(
            "cast_to_tv.casting.pychromecast.get_chromecast_from_cast_info",
        )
        # Fail if either deprecated path is reintroduced, even indirectly.
        self.patch("pychromecast.get_chromecasts", side_effect=AssertionError("legacy helper"))
        self.patch("pychromecast.discover_chromecasts", side_effect=AssertionError("deprecated"))
        self.patch("pychromecast.discovery.discover_chromecasts",
                   side_effect=AssertionError("deprecated"))

    def patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_discovers_devices_and_transfers_live_browser_to_caller(self):
        info = SimpleNamespace(uuid="receiver-id", friendly_name="TV")
        device = Mock(cast_info=info)
        self.device_factory.return_value = device

        def discover_during_scan(seconds):
            self.browser.start_discovery.assert_called_once_with()
            self.browser.devices[info.uuid] = info

        self.sleep.side_effect = discover_during_scan
        devices, browser = discover_cast_devices(timeout=6)

        self.assertEqual(devices, [device])
        self.assertIs(devices[0].cast_info, info)
        self.assertIs(browser, self.browser)
        listener, zconf = self.browser_factory.call_args.args
        self.assertIsInstance(listener, SimpleCastListener)
        self.assertIs(zconf, self.zconf)
        self.sleep.assert_called_once_with(6)
        self.device_factory.assert_called_once_with(info, self.zconf, timeout=6)
        self.browser.stop_discovery.assert_not_called()
        self.zconf.close.assert_not_called()
        device.disconnect.assert_not_called()

    def test_empty_scan_returns_live_browser(self):
        self.assertEqual(discover_cast_devices(), ([], self.browser))
        self.sleep.assert_called_once_with(5)
        self.device_factory.assert_not_called()
        self.browser.stop_discovery.assert_not_called()
        self.zconf.close.assert_not_called()

    def test_unavailable_device_does_not_hide_other_devices(self):
        self.browser.devices = {"gone": Mock(), "available": Mock()}
        device = Mock()
        self.device_factory.side_effect = [ChromecastConnectionError("unavailable"), device]

        self.assertEqual(discover_cast_devices(), ([device], self.browser))
        self.assertEqual(self.device_factory.call_count, 2)
        self.browser.stop_discovery.assert_not_called()

    def test_browser_constructor_failure_closes_zeroconf(self):
        failure = RuntimeError("browser creation failed")
        self.browser_factory.side_effect = failure

        with self.assertRaisesRegex(CastingError, "browser creation failed") as caught:
            discover_cast_devices()

        self.assertIs(caught.exception.__cause__, failure)
        self.zconf.close.assert_called_once_with()

    def test_discovery_start_failure_closes_resources(self):
        self.browser.start_discovery.side_effect = RuntimeError("start failed")

        with self.assertRaisesRegex(CastingError, "start failed"):
            discover_cast_devices()

        self.sleep.assert_not_called()
        self.browser.stop_discovery.assert_called_once_with()
        self.zconf.close.assert_called_once_with()

    def test_partial_scan_failure_disconnects_devices_before_browser(self):
        calls = []
        device = Mock()
        device.disconnect.side_effect = lambda **kwargs: calls.append("disconnect")
        self.browser.stop_discovery.side_effect = lambda: calls.append("stop")
        self.browser.devices = {"first": Mock(), "second": Mock()}
        self.device_factory.side_effect = [device, RuntimeError("device creation failed")]

        with self.assertRaisesRegex(CastingError, "device creation failed"):
            discover_cast_devices()

        self.assertEqual(calls, ["disconnect", "stop"])
        device.disconnect.assert_called_once_with(timeout=5)
        self.zconf.close.assert_called_once_with()

    def test_cleanup_failure_preserves_original_error_and_closes_zeroconf(self):
        self.browser.start_discovery.side_effect = RuntimeError("original failure")
        self.browser.stop_discovery.side_effect = RuntimeError("cleanup failed")

        with self.assertLogs("cast_to_tv.casting", level="ERROR"):
            with self.assertRaisesRegex(CastingError, "original failure"):
                discover_cast_devices()

        self.zconf.close.assert_called_once_with()
