"""Discover saved Nilaris sessions from local files."""

import json
import math
import subprocess
from pathlib import Path


from app_paths import RECORDINGS_DIR, OUTPUTS_DIR
from session_titles import read_title, read_title_job


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
    """Discover sessions from recordings and saved transcripts."""

    recordings = {
        path.stem: path
        for path in RECORDINGS_DIR.glob("nilaris_*.caf")
    } if RECORDINGS_DIR.exists() else {}

    session_ids = set(recordings)

    if OUTPUTS_DIR.exists():
        for session_dir in OUTPUTS_DIR.glob("nilaris_*_separated"):
            if not session_dir.is_dir():
                continue

            session_id = session_dir.name.removesuffix("_separated")

            transcript_txt = session_dir / "transcript.txt"
            transcript_json = session_dir / "transcript.json"

            if transcript_txt.is_file() and transcript_json.is_file():
                session_ids.add(session_id)

    sessions = []

    for session_id in session_ids:
        recording = recordings.get(session_id)
        session_dir = OUTPUTS_DIR / f"{session_id}_separated"

        transcript_txt = session_dir / "transcript.txt"
        transcript_json = session_dir / "transcript.json"
        metadata = session_dir / "session_metadata.json"

        has_audio = recording is not None
        has_transcript = (
            transcript_txt.is_file()
            and transcript_json.is_file()
        )

        if has_transcript:
            status = "transcribed"
        elif session_dir.is_dir():
            status = "prepared"
        else:
            status = "saved"

        if has_audio:
            duration = get_recording_duration(recording, metadata)
            size_mb = round(recording.stat().st_size / (1024 * 1024), 2)
        else:
            duration = get_recording_duration(
                RECORDINGS_DIR / f"{session_id}.caf",
                metadata
            )
            size_mb = 0

        sessions.append({
            **read_title(session_dir),
            **read_title_job(session_dir),
            "session_id": session_id,
            "status": status,
            "recording_path": str(recording) if has_audio else None,
            "duration_seconds": duration,
            "recording_size_mb": size_mb,
            "has_audio": has_audio,
            "has_metadata": metadata.is_file(),
            "has_transcript": has_transcript,
        })

    sessions.sort(
        key=lambda session: session["session_id"],
        reverse=True
    )

    return sessions
