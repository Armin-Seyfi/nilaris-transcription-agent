from pathlib import Path
import subprocess

from app_paths import PROJECT_ROOT, DATA_ROOT, RECORDINGS_DIR
SWIFT_RECORDER = PROJECT_ROOT / "src" / "native_recorder.swift"


def record_meeting():
    """Start Swift recording, wait for the user, and stop safely."""

    RECORDINGS_DIR.mkdir(exist_ok=True)

    # Identify existing recordings so we can find the new one.
    existing_files = set(RECORDINGS_DIR.glob("nilaris_*.caf"))

    print("\nNilaris Transcription Agent")
    print("---------------------------")
    print("Starting native audio recorder...\n", flush=True)

    process = subprocess.Popen(
        ["swift", str(SWIFT_RECORDER)],
        cwd=DATA_ROOT,
        stdin=subprocess.PIPE,
    )

    try:
        print("\nType STOP and press ENTER to finish recording.")

        while input("Command: ").strip().upper() != "STOP":
            print("Recording continues. Type STOP to finish.")

        # Send the explicit stop command to Swift.
        process.stdin.write(b"STOP\n")
        process.stdin.flush()

        return_code = process.wait(timeout=30)

        if return_code != 0:
            raise RuntimeError(
                f"Swift recorder exited with code {return_code}"
            )

    except KeyboardInterrupt:
        print("\nInterrupted. Attempting to stop the recorder...")

        if process.poll() is None:
            process.stdin.write(b"STOP\n")
            process.stdin.flush()
            process.wait(timeout=30)

        raise

    finally:
        if process.stdin:
            process.stdin.close()

    # Find the recording created by this session.
    new_files = set(RECORDINGS_DIR.glob("nilaris_*.caf")) - existing_files

    if len(new_files) != 1:
        raise RuntimeError(
            f"Expected one new recording, found {len(new_files)}."
        )

    recording = new_files.pop()

    print("\nRecording completed successfully.")
    print(f"Saved file: {recording}")
    print(f"File size: {recording.stat().st_size / 1024 / 1024:.2f} MB")

    return recording


if __name__ == "__main__":
    record_meeting()
