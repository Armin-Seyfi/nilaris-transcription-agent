"""Validate that prepared audio covers the complete recording."""

import csv
import math
from pathlib import Path

from transcription_orchestrator import get_duration


CHUNK_BOUNDARY_TOLERANCE_SECONDS = 1.0
END_OF_RECORDING_TOLERANCE_SECONDS = 5.0


def validate_audio_completeness(recording_path, session_dir):
    """Check that LOCAL and REMOTE chunks cover the original recording."""

    recording_path = Path(recording_path)
    session_dir = Path(session_dir)

    duration = get_duration(recording_path)

    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid original recording duration.")

    for source in ("local", "remote"):
        source_dir = session_dir / source
        csv_path = source_dir / "chunks.csv"

        if not csv_path.is_file():
            raise ValueError(f"{source}: chunks.csv is missing.")

        with csv_path.open(newline="") as file:
            rows = list(csv.reader(file))

        if not rows:
            raise ValueError(f"{source}: chunks.csv is empty.")

        previous_end = 0.0

        for index, row in enumerate(rows):
            if len(row) != 3:
                raise ValueError(
                    f"{source}: invalid CSV row {index + 1}."
                )

            filename, start_text, end_text = row

            try:
                start = float(start_text)
                end = float(end_text)
            except ValueError:
                raise ValueError(
                    f"{source}: invalid timestamps in row {index + 1}."
                ) from None

            if (
                not math.isfinite(start)
                or not math.isfinite(end)
                or start < 0
                or end <= start
            ):
                raise ValueError(
                    f"{source}: invalid time range in row {index + 1}."
                )

            audio_path = source_dir / filename

            if not audio_path.is_file():
                raise ValueError(
                    f"{source}: missing audio chunk {filename}."
                )

            if abs(start - previous_end) > CHUNK_BOUNDARY_TOLERANCE_SECONDS:
                raise ValueError(
                    f"{source}: gap or overlap before chunk {index + 1}."
                )

            previous_end = end

        if abs(previous_end - duration) > END_OF_RECORDING_TOLERANCE_SECONDS:
            raise ValueError(
                f"{source}: incomplete audio coverage. "
                f"Expected {duration:.2f}s, got {previous_end:.2f}s."
            )

    return True
