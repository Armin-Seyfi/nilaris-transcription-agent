"""Discover saved Nilaris sessions from local files."""

import json
import math
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RECORDINGS_DIR = PROJECT_ROOT / "recordings"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"


def get_recording_duration(recording, metadata_path):
    """Read duration from metadata, falling back to local ffprobe."""

    if metadata_path.is_file():
        try:
            data = json.loads(
                metadata_path.read_text(encoding="utf-8")
            )

            duration = data.get("duration_seconds")

            if (
                isinstance(duration, (int, float))
                and not isinstance(duration, bool)
                and math.isfinite(duration)
                and duration >= 0
            ):
                return round(duration, 2)

        except (OSError, ValueError, TypeError):
            pass

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(recording),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )

        duration = float(result.stdout.strip())

        if math.isfinite(duration) and duration >= 0:
            return round(duration, 2)

    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        ValueError,
        OSError,
    ):
        pass

    return None


def list_sessions():
    """Return locally saved sessions, newest first."""

    sessions = []

    if not RECORDINGS_DIR.exists():
        return sessions

    for recording in RECORDINGS_DIR.glob("nilaris_*.caf"):

        session_id = recording.stem
        session_dir = OUTPUTS_DIR / f"{session_id}_separated"

        transcript_txt = session_dir / "transcript.txt"
        transcript_json = session_dir / "transcript.json"
        metadata = session_dir / "session_metadata.json"

        if transcript_txt.is_file() and transcript_json.is_file():
            status = "transcribed"
        elif session_dir.is_dir():
            status = "prepared"
        else:
            status = "saved"

        sessions.append({
            "session_id": session_id,
            "status": status,
            "recording_path": str(recording),
            "duration_seconds": get_recording_duration(
                recording, metadata
            ),
            "recording_size_mb": round(
                recording.stat().st_size / (1024 * 1024), 2
            ),
            "has_metadata": metadata.is_file(),
            "has_transcript": (
                transcript_txt.is_file()
                and transcript_json.is_file()
            ),
        })

    sessions.sort(
        key=lambda session: session["session_id"],
        reverse=True
    )

    return sessions
