import csv
import json
import math
import sys
from pathlib import Path

from completion_validator import validate_session
from timestamp_alignment import audio_timing, repair_segment


def format_time(seconds):
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def load_session_segments(session_dir):
    session_dir = Path(session_dir).resolve()
    all_segments = []
    session_duration = None
    metadata_path = session_dir / "session_metadata.json"
    if metadata_path.is_file():
        value = json.loads(metadata_path.read_text(encoding="utf-8")).get("duration_seconds")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
            session_duration = float(value)

    for source in ("local", "remote"):
        source_dir = session_dir / source
        csv_path = source_dir / "chunks.csv"

        if not csv_path.is_file():
            raise FileNotFoundError(csv_path)

        with csv_path.open(newline="") as file:
            rows = list(csv.reader(file))

        for filename, start, end in rows:
            audio_path = source_dir / filename
            json_path = audio_path.with_suffix(".json")

            if not audio_path.is_file():
                raise FileNotFoundError(audio_path)

            start_offset = float(start)
            chunk_end = float(end)

            if (not math.isfinite(start_offset) or not math.isfinite(chunk_end)
                    or start_offset < 0 or chunk_end <= start_offset):
                raise ValueError(f"Invalid chunk timing: {csv_path}")

            # No JSON means this chunk was not transcribed.
            # A silent chunk can legitimately have no JSON.
            if not json_path.exists():
                continue

            data = json.loads(json_path.read_text(encoding="utf-8"))
            audio_duration, silences = audio_timing(audio_path)
            max_duration = min(audio_duration, chunk_end - start_offset)
            if session_duration is not None:
                max_duration = min(max_duration, session_duration - start_offset)
            if max_duration <= 0:
                raise ValueError(f"Chunk lies outside session timing: {audio_path}")

            for segment in data.get("segments", []):
                text = segment["text"].strip()

                if not text:
                    continue

                repaired_start, repaired_end, timing = repair_segment(
                    segment["start"], segment["end"],
                    duration=max_duration, silences=silences,
                )
                global_start = start_offset + repaired_start
                global_end = start_offset + repaired_end
                timing["chunk_offset_seconds"] = start_offset

                if global_start < 0 or global_end < global_start:
                    raise ValueError(f"Invalid segment: {json_path}")

                all_segments.append({
                    "start": round(global_start, 3),
                    "end": round(global_end, 3),
                    "source": source.upper(),
                    "text": text,
                    "timestamp_provenance": timing,
                })

    all_segments.sort(
        key=lambda segment: (
            segment["start"],
            segment["end"],
            segment["source"]
        )
    )

    return all_segments


def build_session_transcript(session_dir):
    session_dir = Path(session_dir).resolve()

    validation = validate_session(session_dir)
    if not validation['complete']:
        raise RuntimeError(
            'Cannot build an incomplete transcript. '
            f'Pending chunks: {validation["pending"]}'
        )

    segments = load_session_segments(session_dir)

    if not segments:
        raise RuntimeError("No transcribed segments found.")

    json_path = session_dir / "transcript.json"
    txt_path = session_dir / "transcript.txt"

    if json_path.exists() or txt_path.exists():
        raise FileExistsError(
            "Final transcript already exists. No files overwritten."
        )

    result = {
        "format_version": "1.0",
        "session_id": session_dir.name,
        "timestamp_unit": "seconds",
        "timestamp_alignment": {
            "method": "conservative_near_silence_boundary_v1",
            "precision": "approximate_segment_boundaries",
            "raw_chunk_json_preserved": True,
            "text_rewritten": False,
        },
        "segment_count": len(segments),
        "segments": segments,
    }

    lines = [
        "NILARIS — MEETING TRANSCRIPT",
        f"Session: {session_dir.name}",
        "Time format: HH:MM:SS",
        "Sources: LOCAL / REMOTE",
        "Timestamps are approximate segment boundaries.",
        "",
    ]

    current_minute = None

    for segment in segments:
        minute = int(segment["start"] // 60)

        if minute != current_minute:
            current_minute = minute

            lines.extend([
                "",
                "=" * 48,
                f"[{format_time(minute * 60)} – "
                f"{format_time((minute + 1) * 60)}]",
                "=" * 48,
                "",
            ])

        lines.append(
            f"[{format_time(segment['start'])}] "
            f"[{segment['source']}] "
            f"{segment['text']}"
        )

    # Exclusive file creation prevents overwriting existing results.
    with json_path.open("x", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)

    with txt_path.open("x", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")

    print("\nUnified transcript created.")
    print(f"Segments: {len(segments)}")
    print(f"TXT: {txt_path}")
    print(f"JSON: {json_path}")

    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(
            "Usage: python src/transcript_builder.py "
            "<session_directory>"
        )
        sys.exit(1)

    build_session_transcript(sys.argv[1])
