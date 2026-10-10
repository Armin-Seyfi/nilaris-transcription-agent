import re
import subprocess
import sys
from pathlib import Path


def measure_audio_volume(audio_path):
    """Measure mean and peak audio volume using FFmpeg."""

    audio_path = Path(audio_path)

    if not audio_path.is_file():
        raise FileNotFoundError(audio_path)

    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i", str(audio_path),
        "-af", "volumedetect",
        "-f", "null",
        "-"
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True
    )

    output = result.stderr

    mean_match = re.search(
        r"mean_volume:\s*(-?[\d.]+)\s*dB",
        output
    )

    max_match = re.search(
        r"max_volume:\s*(-?[\d.]+)\s*dB",
        output
    )

    if not mean_match or not max_match:
        raise RuntimeError("Could not read FFmpeg volume measurements.")

    return {
        "mean_db": float(mean_match.group(1)),
        "max_db": float(max_match.group(1))
    }


def is_silent(audio_path, threshold_db=-60.0):
    """Return True for nearly silent audio."""

    measurements = measure_audio_volume(audio_path)

    return measurements["max_db"] < threshold_db


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python src/audio_quality.py <audio.mp3>")
        sys.exit(1)

    audio = Path(sys.argv[1])
    measurements = measure_audio_volume(audio)

    print(f"File: {audio.name}")
    print(f"Mean volume: {measurements['mean_db']} dBFS")
    print(f"Max volume: {measurements['max_db']} dBFS")
    print(f"Silent: {is_silent(audio)}")
