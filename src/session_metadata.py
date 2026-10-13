import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from capture_metadata import load_capture_metadata


def get_audio_duration(audio_path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path)
        ],
        capture_output=True,
        text=True,
        check=True
    )
    return float(result.stdout.strip())


def create_session_metadata(recording_path):
    recording = Path(recording_path).resolve()

    if not recording.is_file():
        raise FileNotFoundError(recording)

    timestamp_text = recording.stem.removeprefix("nilaris_")

    start_utc = datetime.strptime(
        timestamp_text,
        "%Y-%m-%dT%H-%M-%SZ"
    ).replace(tzinfo=timezone.utc)

    start_local = start_utc.astimezone(
        ZoneInfo("Europe/Berlin")
    )

    session_dir = (
        recording.parent.parent
        / "outputs"
        / f"{recording.stem}_separated"
    )

    if not session_dir.is_dir():
        raise FileNotFoundError(session_dir)

    capture = load_capture_metadata(recording)

    metadata = {
        "session_id": recording.stem,
        "display_name": start_local.strftime(
            "Nilaris_%Y-%m-%d_%H-%M-%S"
        ),
        "start_time_utc": start_utc.isoformat(),
        "start_time_local": start_local.isoformat(),
        "timezone": "Europe/Berlin",
        "duration_seconds": round(
            get_audio_duration(recording), 3
        ),
        "recording_file": recording.name,
        "audio_sources": {
            "LOCAL": capture["microphone"]["name"],
            "REMOTE": capture["blackhole"]["name"]
        },
        "capture": capture,
        "transcription_model": "whisper-1",
        "chunk_duration_target_seconds": 300,
        "transcription_status": "not_started",
        "timestamp_grouping_seconds": 60
    }

    output_path = session_dir / "session_metadata.json"

    with output_path.open("x", encoding="utf-8") as file:
        json.dump(
            metadata,
            file,
            ensure_ascii=False,
            indent=2
        )

    print("Session metadata created successfully.")
    print(f"Session: {metadata['display_name']}")
    print(f"Duration: {metadata['duration_seconds']} seconds")
    print(f"File: {output_path}")

    return metadata




def update_transcription_status(session_dir):
    """Update existing metadata using verified session outputs."""
    import json
    from pathlib import Path
    from completion_validator import validate_session

    session_dir = Path(session_dir).resolve()
    metadata_path = session_dir / "session_metadata.json"

    if not metadata_path.is_file():
        raise FileNotFoundError(
            f"Session metadata does not exist: {metadata_path}"
        )

    validation = validate_session(session_dir)

    txt_exists = (session_dir / "transcript.txt").is_file()
    json_exists = (session_dir / "transcript.json").is_file()

    if txt_exists != json_exists:
        raise RuntimeError(
            "Only one final transcript file exists. "
            "Manual inspection required."
        )

    if validation["complete"] and txt_exists and json_exists:
        status = "completed"
    else:
        status = "incomplete"

    metadata = json.loads(
        metadata_path.read_text(encoding="utf-8")
    )

    metadata["transcription_status"] = status
    metadata["completed_chunks"] = len(validation["completed"])
    metadata["skipped_chunks"] = len(validation["skipped"])
    metadata["pending_chunks"] = len(validation["pending"])

    metadata["transcript_files"] = (
        ["transcript.txt", "transcript.json"]
        if status == "completed"
        else []
    )

    # Save through a temporary file, then replace atomically.
    temporary_path = metadata_path.with_suffix(".json.tmp")

    if temporary_path.exists():
        raise FileExistsError(temporary_path)

    with temporary_path.open("x", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)

    temporary_path.replace(metadata_path)

    print(f"Metadata updated: {metadata_path}")
    print(f"Transcription status: {status}")

    return metadata


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(
            "Usage: python src/session_metadata.py "
            "<recording.caf>"
        )
        sys.exit(1)

    create_session_metadata(sys.argv[1])
