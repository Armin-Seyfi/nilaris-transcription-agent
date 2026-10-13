"""Safe file inventory for Nilaris session storage."""

import re
from pathlib import Path


from app_paths import RECORDINGS_DIR, OUTPUTS_DIR, AUDIO_EXPORTS_DIR

SESSION_ID_PATTERN = re.compile(
    r"nilaris_\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z"
)


def get_session_files(session_id):
    """Identify session files without modifying anything."""

    if not isinstance(session_id, str):
        raise ValueError("Invalid session ID.")

    if SESSION_ID_PATTERN.fullmatch(session_id) is None:
        raise ValueError("Invalid session ID.")

    recording = RECORDINGS_DIR / f"{session_id}.caf"
    capture_metadata = RECORDINGS_DIR / f"{session_id}.capture.json"
    title_metadata = RECORDINGS_DIR / f"{session_id}.title.json"
    title_job = RECORDINGS_DIR / f"{session_id}.title-job.json"
    session_dir = OUTPUTS_DIR / f"{session_id}_separated"
    legacy_dir = OUTPUTS_DIR / session_id
    audio_exports = AUDIO_EXPORTS_DIR / session_id

    if (
        recording.is_symlink()
        or capture_metadata.is_symlink()
        or title_metadata.is_symlink()
        or title_job.is_symlink()
        or session_dir.is_symlink()
        or legacy_dir.is_symlink()
        or audio_exports.is_symlink()
    ):
        raise ValueError("Symbolic links are not allowed.")

    has_audio = recording.is_file()
    has_outputs = session_dir.is_dir()
    has_legacy_outputs = legacy_dir.is_dir()

    transcript_txt = session_dir / "transcript.txt"
    transcript_json = session_dir / "transcript.json"

    has_transcript = (
        has_outputs
        and transcript_txt.is_file()
        and transcript_json.is_file()
        and not transcript_txt.is_symlink()
        and not transcript_json.is_symlink()
    )

    if not has_audio and not has_outputs and not has_legacy_outputs:
        raise FileNotFoundError("Session not found.")

    return {
        "session_id": session_id,
        "recording": str(recording) if has_audio else None,
        "capture_metadata": str(capture_metadata) if capture_metadata.is_file() else None,
        "title_metadata": str(title_metadata) if title_metadata.is_file() else None,
        "title_job": str(title_job) if title_job.is_file() else None,
        "session_directory": str(session_dir) if has_outputs else None,
        "legacy_directory": str(legacy_dir) if has_legacy_outputs else None,
        "audio_export_directory": str(audio_exports) if audio_exports.is_dir() else None,
        "has_audio": has_audio,
        "has_transcript": has_transcript,
        "recording_size_mb": (
            round(recording.stat().st_size / (1024 * 1024), 2)
            if has_audio else 0
        ),
    }


def _move_to_trash(path):
    """Move one existing file or directory to macOS Trash via Finder."""
    import subprocess

    script = """
    on run argv
        set targetFile to POSIX file (item 1 of argv)
        tell application "Finder"
            delete targetFile
        end tell
    end run
    """

    result = subprocess.run(
        ["osascript", "-e", script, str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0 or path.exists() or path.is_symlink():
        raise RuntimeError(
            f"Could not move to Trash: {path.name}. "
            f"{result.stderr.strip()}"
        )


def _check_safe_path(path, root):
    """Reject paths outside their storage root or involving symlinks."""
    root = Path(root)
    path = Path(path)

    if root.is_symlink():
        raise ValueError("Storage root cannot be a symbolic link.")

    if not path.is_relative_to(root):
        raise ValueError("Path is outside Nilaris storage.")

    relative = path.relative_to(root)
    current = root

    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symbolic links are not allowed.")

    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Path resolves outside Nilaris storage.")


def _check_session_tree(session_dir):
    """Reject a session directory containing symbolic links."""
    if session_dir.is_symlink():
        raise ValueError("Session directory is a symbolic link.")

    if session_dir.is_dir():
        for path in session_dir.rglob("*"):
            if path.is_symlink():
                raise ValueError(
                    "Session contains a symbolic link; deletion blocked."
                )


def _move_all_to_trash(paths):
    """Move each path; report partial failures without claiming success."""
    moved = []

    for path in paths:
        try:
            _move_to_trash(path)
            moved.append(str(path))
        except (OSError, RuntimeError, TimeoutError) as error:
            raise RuntimeError(
                f"Trash operation incomplete. "
                f"Moved {len(moved)} of {len(paths)} items. "
                f"Remaining files were not intentionally removed. "
                f"Error: {error}"
            ) from error

    return {"moved_to_trash": moved, "items_moved": len(moved)}


def remove_session_audio(session_id):
    """Trash audio while retaining a completed transcript."""
    session = get_session_files(session_id)

    if not session["has_transcript"]:
        raise ValueError(
            "Cannot remove audio without a completed transcript."
        )

    paths = []

    if session["recording"]:
        recording = Path(session["recording"])
        _check_safe_path(recording, RECORDINGS_DIR)
        paths.append(recording)

    if session["session_directory"]:
        session_dir = Path(session["session_directory"])
        _check_safe_path(session_dir, OUTPUTS_DIR)
        _check_session_tree(session_dir)

        for folder_name in ("local", "remote"):
            folder = session_dir / folder_name

            if folder.is_dir():
                for audio in sorted(folder.rglob("*.mp3")):
                    _check_safe_path(audio, OUTPUTS_DIR)

                    if audio.is_file():
                        paths.append(audio)

    if session["legacy_directory"]:
        legacy_dir = Path(session["legacy_directory"])
        _check_safe_path(legacy_dir, OUTPUTS_DIR)
        _check_session_tree(legacy_dir)

        for audio in sorted(legacy_dir.rglob("*.mp3")):
            _check_safe_path(audio, OUTPUTS_DIR)

            if audio.is_file():
                paths.append(audio)

    if not paths:
        raise ValueError("No audio files remain in this session.")

    if session['audio_export_directory']:
        folder = Path(session['audio_export_directory'])
        _check_safe_path(folder, AUDIO_EXPORTS_DIR)
        _check_session_tree(folder)
        paths.append(folder)

    return _move_all_to_trash(paths)


def trash_entire_session(session_id):
    """Trash original recording and session output directory."""
    session = get_session_files(session_id)
    paths = []
    if session['audio_export_directory']:
        folder = Path(session['audio_export_directory'])
        _check_safe_path(folder, AUDIO_EXPORTS_DIR)
        _check_session_tree(folder)
        paths.append(folder)
    for key in ('title_metadata', 'title_job'):
        if session[key]:
            path = Path(session[key])
            _check_safe_path(path, RECORDINGS_DIR)
            paths.append(path)

    if session["recording"]:
        recording = Path(session["recording"])
        _check_safe_path(recording, RECORDINGS_DIR)
        paths.append(recording)

    if session["capture_metadata"]:
        capture_metadata = Path(session["capture_metadata"])
        _check_safe_path(capture_metadata, RECORDINGS_DIR)
        paths.append(capture_metadata)

    if session["session_directory"]:
        session_dir = Path(session["session_directory"])
        _check_safe_path(session_dir, OUTPUTS_DIR)
        _check_session_tree(session_dir)
        paths.append(session_dir)

    if session["legacy_directory"]:
        legacy_dir = Path(session["legacy_directory"])
        _check_safe_path(legacy_dir, OUTPUTS_DIR)
        _check_session_tree(legacy_dir)
        paths.append(legacy_dir)

    return _move_all_to_trash(paths)
