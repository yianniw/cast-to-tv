"""Live native sender bridge; preserves the experimental CLI entry point."""
from __future__ import annotations

import json
import logging
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from .errors import CastingCancelled, check_cancelled
from .paths import temporary_directory
from .recovery import ReceiverOwnership, RecoveryError, native_failure, prepare_recovery_receiver
from .native.patterns import generate_clip
from .native.protocol import APP_ID, NAMESPACE, PROTOCOL_VERSION, StreamingController, validate_event
from .receivers.discovery import wait_for_cast


def run_probe(device, helper: Path, clip: Path, playout: int, events: queue.Queue,
              cancel: threading.Event, report: Path, desktop: dict | None = None,
              audio_clip: Path | None = None, on_event: Callable[[dict], None] | None = None,
              system_audio: bool = False, *, preserve_receiver: bool | ReceiverOwnership = False) -> None:
    controller = StreamingController()
    process = None
    readers: list[threading.Thread] = []
    launched = False
    destination = None
    records = []
    configuration_directory = None
    reader_stop = threading.Event()
    start = time.perf_counter()
    try:
        if cancel.is_set():
            return
        try:
            wait_for_cast(device, cancel, timeout=15)
        except CastingCancelled:
            raise
        except Exception as exc:
            raise RecoveryError(f"Could not connect to the TV: {exc}", "connection") from exc
        check_cancelled(cancel)
        if preserve_receiver:
            prepare_recovery_receiver(
                device, preserve_receiver if isinstance(preserve_receiver, ReceiverOwnership) else None, cancel,
            )
        if device.app_id == APP_ID:
            device.quit_app(timeout=10)
        check_cancelled(cancel)
        device.register_handler(controller)
        launch_response = threading.Event()
        launch_success: list[bool] = []

        def launched_response(success: bool, _data) -> None:
            launch_success.append(success)
            launch_response.set()

        controller.launch(force_launch=True, callback_function=launched_response)
        launched = True
        deadline = time.monotonic() + 20
        while True:
            if cancel.is_set():
                return
            if launch_response.is_set() and not launch_success[-1]:
                raise RuntimeError("The TV rejected launching its mirroring receiver")
            if (controller.is_active and device.app_id == APP_ID
                    and device.socket_client.destination_id):
                # An already-open route can expose namespaces without calling
                # channel_connected on a newly registered controller.
                controller.connected.set()
                break
            if time.monotonic() > deadline:
                raise RuntimeError(f"Native receiver launch timed out (app={device.app_id}, "
                                   f"namespaces={device.socket_client.app_namespaces}, "
                                   f"launch_response={launch_success})")
            cancel.wait(.05)
        destination = device.socket_client.destination_id
        if on_event is not None:
            on_event({"event": "receiver_ready", "destination": destination})
        check_cancelled(cancel)
        if device.app_id != APP_ID or not destination:
            raise RuntimeError("The TV did not launch the requested mirroring receiver")
        arguments = [str(helper.resolve()), device.cast_info.host, device.socket_client.source_id,
                     destination, str(clip.resolve()), str(playout)]
        if desktop is not None:
            configuration_directory = tempfile.TemporaryDirectory(prefix="native-capture-", dir=temporary_directory())
            configuration = Path(configuration_directory.name) / "capture.json"
            configuration.write_text(json.dumps(desktop), encoding="utf-8")
            arguments[4] = str(configuration)
            arguments.append("--desktop-config")
        if audio_clip is not None:
            arguments.extend(["--audio-clip", str(audio_clip.resolve())])
        if system_audio:
            arguments.append("--system-audio")
        check_cancelled(cancel)
        process = subprocess.Popen(
            arguments, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW)
        output: queue.Queue[str | None] = queue.Queue(maxsize=256)
        errors: list[str] = []

        def read_stdout() -> None:
            for line in process.stdout:
                # The queue is bounded; cancellation also releases this reader.
                while not reader_stop.is_set():
                    try:
                        output.put(line, timeout=.1)
                        break
                    except queue.Full:
                        continue
            while not reader_stop.is_set():
                try:
                    output.put(None, timeout=.1)
                    return
                except queue.Full:
                    continue

        def read_stderr() -> None:
            for line in process.stderr:
                errors.append(line.rstrip())
                del errors[:-40]

        readers = [threading.Thread(target=read_stdout, daemon=True),
                   threading.Thread(target=read_stderr, daemon=True)]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + 15
        negotiated = False
        finished = False
        while not cancel.is_set():
            if controller.failed.is_set():
                raise RuntimeError("Cast control message queue overflowed")
            if not controller.connected.is_set():
                raise RecoveryError("Native mirroring control channel disconnected", "connection")
            while True:
                try:
                    message = controller.messages.get_nowait()
                except queue.Empty:
                    break
                process.stdin.write(json.dumps(message) + "\n")
                process.stdin.flush()
            try:
                line = output.get(timeout=.05)
            except queue.Empty:
                if not negotiated and time.monotonic() > deadline:
                    raise RuntimeError("Native sender negotiation timed out")
                continue
            if line is None:
                code = process.wait(timeout=5)
                if code or not finished:
                    raise RuntimeError(f"Native helper ended unexpectedly ({code}): {'; '.join(errors[-3:])}")
                break
            if len(line) > 1024 * 1024:
                raise RuntimeError("Native helper event exceeds size limit")
            event = json.loads(line)
            validate_event(event, destination)
            if event["event"] == "message":
                controller.send_message(event["payload"], no_add_request_id=True)
                # OFFER contains ephemeral encryption secrets; store only type.
                record = {"event": "control_sent", "type": event["payload"].get("type")}
            else:
                record = dict(event)
                if event["event"] == "error":
                    raise native_failure(event.get("message", "Native sender failed"))
                if event["event"] == "negotiated":
                    negotiated = True
                    events.put(("clock_start", time.perf_counter() + .1))
                elif event["event"] == "finished":
                    finished = True
            record["elapsed_seconds"] = time.perf_counter() - start
            records.append(record)
            if len(records) > 10000:
                del records[10]
            if on_event is not None:
                on_event(record)
    except CastingCancelled:
        pass
    except Exception as exc:
        records.append({"event": "bridge_error", "message": str(exc),
                        "elapsed_seconds": time.perf_counter() - start})
        raise
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    process.stdin.write('{"version":1,"command":"stop"}\n')
                    process.stdin.flush()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    process.terminate()
                    process.wait(timeout=5)
            reader_stop.set()
            for reader in readers:
                reader.join(timeout=2)
            for pipe in (process.stdin, process.stdout, process.stderr):
                pipe.close()
        if configuration_directory is not None:
            configuration_directory.cleanup()
        # Quit only the mirroring app launched here, if it still owns this route.
        try:
            if launched and destination is None and device.app_id != APP_ID:
                if not launch_response.wait(timeout=10):
                    raise RuntimeError("The TV has not confirmed the cancelled mirroring receiver launch.")
            if launched and device.socket_client.is_connected and device.app_id == APP_ID and (
                    destination is None or device.socket_client.destination_id == destination):
                device.quit_app()
        except Exception as exc:
            logging.exception("Could not stop the native receiver")
            record = {"event": "cleanup_error", "message": str(exc)}
            records.append(record)
            if on_event is not None:
                on_event(record)
        finally:
            device.socket_client.unregister_handler(controller)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({"prototype": "Open Screen VP8 sender",
                                      "audio": "system Opus" if system_audio else "synthetic Opus" if audio_clip else "none",
                                      "source": desktop or {"kind": "test pattern"},
                                      "requested_playout_ms": playout,
                                      "visible_latency": "requires simultaneous PC/TV clock readings",
                                      "events": records}, indent=2), encoding="utf-8")



def main() -> None:
    """Run the opt-in experiment without loading its UI during normal casting."""
    from .native.cli import main as run_cli

    run_cli()


if __name__ == "__main__":
    main()
