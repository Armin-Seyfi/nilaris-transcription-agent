import argparse
import csv
import json
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from audio_quality import is_silent
from completion_validator import validate_session
from transcript_builder import build_session_transcript
from session_metadata import update_transcription_status

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

MODEL = "whisper-1"
PRICE_PER_MINUTE = 0.006


def get_duration(audio_path):
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


def inspect_session(session_dir):
    session_dir = Path(session_dir).resolve()

    if not session_dir.is_dir():
        raise FileNotFoundError(session_dir)

    ready_chunks = []
    skipped_chunks = []
    completed_chunks = []

    for source in ("local", "remote"):
        source_dir = session_dir / source
        csv_path = source_dir / "chunks.csv"

        if not csv_path.is_file():
            raise FileNotFoundError(csv_path)

        with csv_path.open(newline="") as file:
            rows = list(csv.reader(file))

        for filename, start, end in rows:
            audio_path = source_dir / filename

            if not audio_path.is_file():
                raise FileNotFoundError(audio_path)

            start = float(start)
            end = float(end)

            if start < 0 or end <= start:
                raise ValueError(
                    f"Invalid chunk timestamps: {audio_path}"
                )

            chunk = {
                "source": source.upper(),
                "path": audio_path,
                "json_path": audio_path.with_suffix(".json"),
                "start_offset_seconds": start,
                "end_offset_seconds": end,
                "duration_seconds": get_duration(audio_path),
            }

            if chunk["json_path"].exists():
                completed_chunks.append(chunk)
                print(f"CACHED [{source.upper()}] {filename}")

            elif is_silent(audio_path):
                skipped_chunks.append(chunk)
                print(f"SKIP [{source.upper()}] {filename}: silent")

            else:
                ready_chunks.append(chunk)
                print(
                    f"READY [{source.upper()}] {filename}: "
                    f"starts at {start:.3f}s"
                )

    estimated_minutes = sum(
        chunk["duration_seconds"] / 60
        for chunk in ready_chunks
    )

    estimated_cost = estimated_minutes * PRICE_PER_MINUTE

    print("\n=== TRANSCRIPTION PLAN ===")
    print(f"Session: {session_dir.name}")
    print(f"Ready chunks: {len(ready_chunks)}")
    print(f"Cached chunks: {len(completed_chunks)}")
    print(f"Skipped chunks: {len(skipped_chunks)}")
    print(f"Estimated audio minutes: {estimated_minutes:.2f}")
    print(f"Estimated Whisper cost: ${estimated_cost:.5f}")

    return ready_chunks


def transcribe_chunks(chunks):
    client = OpenAI()

    for chunk in chunks:
        audio_path = chunk["path"]
        json_path = chunk["json_path"]

        if json_path.exists():
            raise FileExistsError(json_path)

        if audio_path.stat().st_size >= 25 * 1024 * 1024:
            raise ValueError(
                f"Audio exceeds upload limit: {audio_path}"
            )

        print(
            f"\nTranscribing [{chunk['source']}]: "
            f"{audio_path.name}",
            flush=True
        )

        with audio_path.open("rb") as audio_file:
            result = client.audio.transcriptions.create(
                model=MODEL,
                file=audio_file,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )

        data = result.model_dump(mode="json")

        # Exclusive creation prevents accidental overwriting.
        with json_path.open("x", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2
            )

        print(f"Saved: {json_path}")

    print("\nTranscription requests completed.")




def finalize_session(session_dir):
    """Validate and finalize a fully transcribed session."""
    session_dir = Path(session_dir).resolve()

    validation = validate_session(session_dir)

    if not validation["complete"]:
        raise RuntimeError(
            "Session transcription is incomplete. "
            f"Pending: {validation['pending']}"
        )

    txt_path = session_dir / "transcript.txt"
    json_path = session_dir / "transcript.json"

    if txt_path.exists() and json_path.exists():
        print("Final transcript already exists. No overwrite.")
    elif txt_path.exists() or json_path.exists():
        raise RuntimeError(
            "Only one final transcript file exists. "
            "Manual inspection required."
        )
    else:
        build_session_transcript(session_dir)

    update_transcription_status(session_dir)

    return {
        "state": "completed",
        "session_dir": str(session_dir),
        "transcript_txt": str(txt_path),
        "transcript_json": str(json_path),
        "completed_chunks": len(validation["completed"]),
        "skipped_chunks": len(validation["skipped"]),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "session_dir",
        help="Nilaris separated-session directory"
    )

    parser.add_argument(
        "--send",
        action="store_true",
        help="Execute paid Whisper API requests"
    )

    args = parser.parse_args()

    ready = inspect_session(args.session_dir)

    if args.send:
        if ready:
            transcribe_chunks(ready)
        else:
            print("\nNo new chunks require transcription.")

        validation = validate_session(args.session_dir)

        if not validation["complete"]:
            raise RuntimeError(
                "Session transcription is incomplete. "
                f"Pending: {validation['pending']}"
            )

        session_dir = Path(args.session_dir).resolve()
        txt_path = session_dir / "transcript.txt"
        json_path = session_dir / "transcript.json"

        if txt_path.exists() and json_path.exists():
            print("\nFinal transcript already exists. No overwrite.")
        elif txt_path.exists() or json_path.exists():
            raise RuntimeError(
                "Only one final transcript file exists. "
                "Manual inspection required."
            )
        else:
            build_session_transcript(session_dir)

        update_transcription_status(session_dir)

        print("\nSESSION PROCESSING COMPLETED.")
    else:
        print("\nDRY RUN: No API requests were sent.")
