import csv
import json
import sys
from pathlib import Path

from audio_quality import is_silent


def validate_session(session_dir):
    session_dir = Path(session_dir).resolve()

    if not session_dir.is_dir():
        raise FileNotFoundError(session_dir)

    completed = []
    skipped = []
    pending = []

    for source in ("local", "remote"):
        folder = session_dir / source
        csv_path = folder / "chunks.csv"

        if not csv_path.is_file():
            raise FileNotFoundError(csv_path)

        with csv_path.open(newline="") as file:
            rows = list(csv.reader(file))

        if not rows:
            raise ValueError(f"Empty chunk list: {csv_path}")

        for filename, start, end in rows:
            audio = folder / filename
            transcript = audio.with_suffix(".json")
            label = f"{source}/{filename}"

            if not audio.is_file():
                raise FileNotFoundError(audio)

            if float(start) < 0 or float(end) <= float(start):
                raise ValueError(f"Invalid chunk timing: {label}")

            if transcript.exists():
                try:
                    data = json.loads(
                        transcript.read_text(encoding="utf-8")
                    )

                    segments = data.get("segments")

                    if not isinstance(segments, list):
                        raise ValueError("Missing segments")

                    for segment in segments:
                        segment_start = float(segment["start"])
                        segment_end = float(segment["end"])

                        if segment_start < 0 or segment_end < segment_start:
                            raise ValueError("Invalid segment timing")

                        if not isinstance(segment["text"], str):
                            raise ValueError("Invalid segment text")

                    completed.append(label)

                except (ValueError, KeyError, TypeError) as error:
                    pending.append(f"{label} (invalid JSON: {error})")

            elif is_silent(audio):
                skipped.append(label)

            else:
                pending.append(label)

    result = {
        "complete": len(pending) == 0,
        "completed": completed,
        "skipped": skipped,
        "pending": pending,
    }

    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(
            "Usage: python src/completion_validator.py "
            "<session_directory>"
        )
        sys.exit(1)

    result = validate_session(sys.argv[1])

    print("\n=== SESSION VALIDATION ===")
    print(f"Completed: {len(result['completed'])}")
    print(f"Skipped: {len(result['skipped'])}")
    print(f"Pending: {len(result['pending'])}")

    for item in result["pending"]:
        print(f"PENDING: {item}")

    print(f"Session complete: {result['complete']}")

    if not result["complete"]:
        sys.exit(1)
