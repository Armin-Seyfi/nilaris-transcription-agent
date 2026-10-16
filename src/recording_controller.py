import subprocess
from macos_notifications import notify_recording_failure
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app_paths import PROJECT_ROOT, DATA_ROOT, RECORDINGS_DIR
from capture_metadata import load_capture_metadata
SWIFT_RECORDER = PROJECT_ROOT / "src" / "native_recorder.swift"

READY_SIGNAL = "NILARIS_READY"
STARTUP_TIMEOUT = 60

DEFAULT_MAX_RECORDING_SECONDS = 3 * 60 * 60
ABSOLUTE_MAX_RECORDING_SECONDS = 9 * 60 * 60


class RecordingController:
    def __init__(self, max_duration_seconds=DEFAULT_MAX_RECORDING_SECONDS):
        if not 0 < max_duration_seconds <= ABSOLUTE_MAX_RECORDING_SECONDS:
            raise ValueError("Recording duration must be between 0 and 9 hours.")

        self.max_duration_seconds = max_duration_seconds
        self.process = None
        self.started_at = None
        self.started_monotonic = None
        self.existing_files = set()

        self.lock = threading.RLock()
        self.ready_event = threading.Event()
        self.output_lines = []

        self.last_completed_recording = None
        self.last_recovery = None
        self.auto_stopped = False
        self.stop_requested = False
        self.listening_device = None
        self.route_warning = None
        self.reader_thread = None

    def _read_output(self, process):
        """Read Swift output continuously and detect recorder readiness."""
        for line in process.stdout:
            line = line.rstrip()
            self.output_lines.append(line)

            if line.startswith("NILARIS_ROUTE_WARNING: "):
                self.route_warning = line.removeprefix("NILARIS_ROUTE_WARNING: ")

            if line.startswith("NILARIS_OUTPUT: "):
                self.listening_device = line.removeprefix("NILARIS_OUTPUT: ")

            if line == READY_SIGNAL:
                self.ready_event.set()
            else:
                print(f"[Swift] {line}", flush=True)

    def start_recording(self):
        with self.lock:
            if self.process is not None:
                raise RuntimeError("A recording is already active or unfinished.")

            RECORDINGS_DIR.mkdir(exist_ok=True)

            self.existing_files = set(
                RECORDINGS_DIR.glob("nilaris_*.caf")
            )

            self.ready_event.clear()
            self.output_lines = []
            self.auto_stopped = False
            self.stop_requested = False
            self.last_completed_recording = None
            self.listening_device = None
            self.route_warning = None

            process = subprocess.Popen(
                ["swift", str(SWIFT_RECORDER)],
                cwd=DATA_ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            self.process = process

            self.reader_thread = threading.Thread(
                target=self._read_output,
                args=(process,),
                daemon=True,
            )
            self.reader_thread.start()

        deadline = time.monotonic() + STARTUP_TIMEOUT

        while time.monotonic() < deadline:
            if self.ready_event.wait(timeout=0.2):
                with self.lock:
                    if self.process is process and process.poll() is None:
                        self.started_at = datetime.now(timezone.utc)
                        self.started_monotonic = time.monotonic()

                        threading.Thread(
                            target=self._recording_watchdog,
                            args=(process,),
                            daemon=True,
                        ).start()

                        return self.get_status()
                break

            if process.poll() is not None:
                break

        with self.lock:
            if process.poll() is None:
                try:
                    process.stdin.write("STOP\n")
                    process.stdin.flush()
                    process.wait(timeout=10)
                except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()

            if self.reader_thread:
                self.reader_thread.join(timeout=2)

            self._restore_audio_route()

            if process.stdin:
                process.stdin.close()

            if self.process is process:
                self.process = None
                self.started_at = None
                self.started_monotonic = None
                self.ready_event.clear()

        details = "\n".join(self.output_lines[-10:])
        raise RuntimeError(
            "Recorder startup failed or timed out.\n"
            f"Recent Swift output:\n{details}"
        )

    def _restore_audio_route(self):
        """Recover only this recorder's journal; respect an output chosen by the user."""
        if not (DATA_ROOT / "audio_route_recovery.json").exists():
            return
        result = subprocess.run(
            ["swift", str(SWIFT_RECORDER), "--recover-route"],
            cwd=DATA_ROOT, capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError("Audio route recovery needs attention: " + result.stdout[-2000:])

    def _recording_watchdog(self, process):
        """Automatically stop and save at the recording duration limit."""
        while True:
            time.sleep(0.5)

            with self.lock:
                if self.process is not process:
                    return

                if process.poll() is not None:
                    if self.stop_requested:
                        return

                    print(
                        "Recorder exited unexpectedly. Sending macOS alert.",
                        flush=True,
                    )

                    threading.Thread(
                        target=notify_recording_failure,
                        daemon=True,
                    ).start()

                    return

                if self.started_monotonic is None:
                    return

                elapsed = time.monotonic() - self.started_monotonic

                if elapsed < self.max_duration_seconds:
                    continue

                print("\nRecording limit reached. Auto-stopping...", flush=True)

                try:
                    result = self.stop_recording()
                    self.auto_stopped = True
                    self.last_completed_recording = result
                    print("Recording automatically saved.", flush=True)
                except Exception as error:
                    print(f"Auto-stop failed: {error}", flush=True)

                return

    def get_status(self):
        with self.lock:
            if self.process is None:
                return {
                    "state": "auto_stopped" if self.auto_stopped else "idle",
                    "elapsed_seconds": 0,
                    "max_duration_seconds": self.max_duration_seconds,
                    "last_completed_recording": self.last_completed_recording,
                }

            exit_code = self.process.poll()

            if exit_code is not None:
                state = "error"
            elif self.ready_event.is_set():
                state = "recording"
            else:
                state = "starting"

            elapsed = (
                max(0, time.monotonic() - self.started_monotonic)
                if self.started_monotonic is not None
                else 0
            )

            return {
                "state": state,
                "elapsed_seconds": round(elapsed, 1),
                "remaining_seconds": round(
                    max(0, self.max_duration_seconds - elapsed), 1
                ),
                "max_duration_seconds": self.max_duration_seconds,
                "pid": self.process.pid,
                "listening_device": self.listening_device,
                "route_warning": self.route_warning,
                "error": (
                    "Audio write failure"
                    if state == "error" and any(
                        ("NILARIS_AUDIO_WRITE_ERROR" in line or "NILARIS_AUDIO_ERROR" in line)
                        for line in self.output_lines
                    )
                    else "Recorder exited unexpectedly"
                    if state == "error"
                    else None
                ),
                "exit_code": exit_code,
            }

    def acknowledge_recorder_error(self):
        """Reset a crashed recorder without deleting any recording files."""
        with self.lock:
            if self.process is None or self.process.poll() is None:
                raise RuntimeError("No stopped recorder to recover.")

            self._restore_audio_route()
            exit_code = self.process.returncode

            new_files = sorted(
                set(RECORDINGS_DIR.glob("nilaris_*.caf"))
                - self.existing_files
            )

            recovery_info = {
                "state": "recovered",
                "exit_code": exit_code,
                "possible_recordings": [str(p) for p in new_files],
                "message": (
                    "Recorder state reset. Existing audio files were preserved "
                    "but have not been validated."
                ),
            }

            self.last_recovery = recovery_info

            if self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()

            self.process = None
            self.started_at = None
            self.started_monotonic = None
            self.existing_files = set()
            self.ready_event.clear()

            return recovery_info

    def stop_recording(self):
        with self.lock:
            if self.process is None:
                raise RuntimeError("No active recording.")

            if not self.ready_event.is_set():
                raise RuntimeError("Recorder is not ready.")

            process = self.process

            if process.poll() is not None:
                raise RuntimeError("Recorder exited unexpectedly.")

            self.stop_requested = True
            process.stdin.write("STOP\n")
            process.stdin.flush()

            return_code = process.wait(timeout=30)
            process.stdin.close()
            if self.reader_thread:
                self.reader_thread.join(timeout=2)
            self._restore_audio_route()

            if return_code != 0:
                raise RuntimeError(
                    f"Recorder exited with code {return_code}"
                )

            new_files = set(
                RECORDINGS_DIR.glob("nilaris_*.caf")
            ) - self.existing_files

            if len(new_files) != 1:
                raise RuntimeError(
                    "Could not uniquely identify the new recording."
                )

            recording_path = new_files.pop()
            capture = load_capture_metadata(recording_path)

            result = {
                "state": "completed",
                "recording_path": str(recording_path),
                "duration_seconds": capture["duration_seconds"],
                "route_warning": self.route_warning,
                "listening_device": self.listening_device,
                "file_size_mb": round(
                    recording_path.stat().st_size / 1024 / 1024, 2
                ),
            }

            self.process = None
            self.started_at = None
            self.started_monotonic = None
            self.existing_files = set()
            self.ready_event.clear()
            self.last_completed_recording = result

            return result
