import argparse
import json
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from app_paths import OUTPUTS_DIR
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def transcribe(audio_path, send=False):
    audio_path = Path(audio_path).resolve()

    if not audio_path.is_file():
        raise FileNotFoundError(audio_path)

    size_mb = audio_path.stat().st_size / (1024 * 1024)

    if size_mb >= 25:
        raise ValueError("Audio file exceeds the 25 MB upload limit.")

    duration = float(subprocess.check_output([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path)
    ], text=True).strip())

    print(f"Audio: {audio_path.name}")
    print(f"Duration: {duration:.2f} seconds")
    print(f"File size: {size_mb:.3f} MB")
    print("Model: whisper-1")
    print(f"Estimated cost: ${duration / 60 * 0.006:.5f}")

    if not send:
        print("\nDRY RUN: No API request was sent.")
        return

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    output_dir = OUTPUTS_DIR / "transcription_tests"
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / f"{audio_path.stem}.json"
    txt_path = output_dir / f"{audio_path.stem}.txt"

    if json_path.exists() or txt_path.exists():
        raise FileExistsError("Transcript already exists; refusing to overwrite.")

    client = OpenAI()

    with audio_path.open("rb") as audio_file:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )

    data = result.model_dump(mode="json")

    json_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    txt_path.write_text(result.text, encoding="utf-8")

    print("\nTranscription completed.")
    print(f"TXT: {txt_path}")
    print(f"JSON: {json_path}")
    print("\nTranscript:\n")
    print(result.text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("audio_file")
    parser.add_argument(
        "--send",
        action="store_true",
        help="Send audio to OpenAI API (paid request)"
    )
    args = parser.parse_args()

    transcribe(args.audio_file, send=args.send)
