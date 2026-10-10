"""System notifications for Nilaris Transcriber."""

import subprocess


def notify_recording_failure():
    """Display a macOS alert when recording fails unexpectedly."""
    script = (
        'display notification '
        '"Recording stopped unexpectedly. Your audio may be incomplete." '
        'with title "Nilaris Transcriber" '
        'subtitle "IMPORTANT RECORDING ALERT" '
        'sound name "Basso"'
    )

    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired) as error:
        print(
            f"[Nilaris] macOS notification failed: {error}",
            flush=True,
        )


if __name__ == "__main__":
    notify_recording_failure()
