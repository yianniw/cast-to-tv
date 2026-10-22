"""Discover Cast receivers and verify their identity before reconnecting."""
from __future__ import annotations

import logging
import socket
import threading
import time

import pychromecast
import zeroconf
from pychromecast.discovery import CastBrowser, SimpleCastListener
from pychromecast.dial import get_device_info
from pychromecast.error import ChromecastConnectionError, RequestTimeout
from pychromecast.models import HostServiceInfo

from ..errors import CastingError, check_cancelled

# Preserve the logger consumed by existing diagnostics and support tooling.
LOGGER = logging.getLogger("cast_to_tv.casting")


def discover_cast_devices(
    timeout: float = 5,
    *, browser_factory=CastBrowser,
) -> tuple[list[pychromecast.Chromecast], CastBrowser]:
    zconf = None
    browser = None
    devices = []
    try:
        zconf = zeroconf.Zeroconf()
        browser = browser_factory(SimpleCastListener(), zconf)
        browser.start_discovery()
        time.sleep(timeout)
        # Keep discovery alive: connections still need its updated mDNS services.
        for info in list(browser.devices.values()):
            try:
                devices.append(pychromecast.get_chromecast_from_cast_info(
                    info, browser.zc, timeout=timeout,
                ))
            except ChromecastConnectionError:
                continue
        return devices, browser
    except Exception as exc:
        for device in devices:
            try:
                device.disconnect(timeout=5)
            except Exception:
                LOGGER.exception("Could not disconnect a device after discovery failed")
        if browser is not None:
            try:
                browser.stop_discovery()
            except Exception:
                LOGGER.exception("Could not stop failed Chromecast discovery")
        if zconf is not None:
            try:
                zconf.close()
            except Exception:
                LOGGER.exception("Could not close failed Chromecast discovery")
        raise CastingError(
            f"Could not discover Chromecast devices: {exc}. "
            "Check that the PC and TV are on the same non-isolated network."
        ) from exc


def wait_for_cast(device, cancel: threading.Event, timeout: float, *, device_info=get_device_info) -> None:
    """Poll Cast readiness without waiting out the entire timeout on cancellation."""
    check_cancelled(cancel)
    # Discovery can withdraw a TV's shared service set when its receiver exits.
    # A new connection needs its own snapshot. If no service remains, verify the
    # known address still belongs to this UUID before using a direct connection.
    services = device.socket_client.services.copy()
    if not services:
        info = device_info(device.cast_info.host, timeout=2)
        check_cancelled(cancel)
        if info is None or info.uuid != device.cast_info.uuid:
            raise CastingError("The TV's address could not be verified. Scan for TVs again.")
        services.add(HostServiceInfo(device.cast_info.host, device.cast_info.port))
    device.socket_client.services = services
    deadline = time.monotonic() + timeout
    while True:
        check_cancelled(cancel)
        try:
            device.wait(timeout=min(.2, max(0, deadline - time.monotonic())))
        except RequestTimeout as exc:
            check_cancelled(cancel)
            if time.monotonic() >= deadline:
                raise RequestTimeout("wait", timeout) from exc
        else:
            check_cancelled(cancel)
            return


def local_address_for(device_host: str) -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route_socket:
            route_socket.connect((device_host, 8009))
            return route_socket.getsockname()[0]
    except OSError as exc:
        raise CastingError(f"Could not find a network route to the TV: {exc}") from exc
