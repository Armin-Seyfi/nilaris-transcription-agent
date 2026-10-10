from pathlib import Path

from audio_processor import process_recording
from session_metadata import create_session_metadata
from transcription_orchestrator import inspect_session, PRICE_PER_MINUTE


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RECORDINGS_DIR = (PROJECT_ROOT / "recordings").resolve()
OUTPUTS_DIR = PROJECT_ROOT / "outputs"


def prepare_session(recording_path):
    """Prepare a saved recording without calling the Whisper API."""

    recording_path = Path(recording_path).resolve()

    # Only accept existing recordings from the Nilaris recordings folder.
    if (
        recording_path.parent != RECORDINGS_DIR
        or recording_path.suffix.lower() != ".caf"
        or not recording_path.is_file()
    ):
        raise ValueError("Invalid Nilaris recording path.")

    session_dir = (
        OUTPUTS_DIR / f"{recording_path.stem}_separated"
    )

    # Do not overwrite audio chunks created by a previous run.
    if not session_dir.exists():
        print("Processing audio streams...", flush=True)

        process_recording(
            recording_path,
            chunk_seconds=300,
        )
    else:
        print("Existing session found. Reusing audio chunks.")

    # Create metadata only when missing.
    metadata_path = session_dir / "session_metadata.json"

    if not metadata_path.exists():
        create_session_metadata(recording_path)

    # Inspect chunks locally; no OpenAI API request is sent.
    ready_chunks = inspect_session(session_dir)

    estimated_minutes = sum(
        chunk["duration_seconds"] / 60
        for chunk in ready_chunks
    )

    return {
        "state": "awaiting_confirmation",
        "recording_path": str(recording_path),
        "session_dir": str(session_dir),
        "ready_chunks": len(ready_chunks),
        "estimated_minutes": round(estimated_minutes, 2),
        "estimated_cost_usd": round(
            estimated_minutes * PRICE_PER_MINUTE,
            5,
        ),
        "api_request_sent": False,
    }
