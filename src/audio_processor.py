from pathlib import Path
import subprocess
import sys

from app_paths import OUTPUTS_DIR as OUTPUT_DIR


def process_recording(recording_path, chunk_seconds=300):
    recording_path = Path(recording_path).resolve()

    if not recording_path.is_file():
        raise FileNotFoundError(recording_path)

    session_dir = OUTPUT_DIR / f"{recording_path.stem}_separated"

    if session_dir.exists():
        raise FileExistsError(
            f"Session output already exists: {session_dir}"
        )

    print(f"\nProcessing: {recording_path.name}", flush=True)

    streams = {
        "local": "pan=mono|c0=c2,highpass=f=100",
        "remote": "pan=mono|c0=0.5*c0+0.5*c1",
    }

    results = {}

    for stream_name, audio_filter in streams.items():
        stream_dir = session_dir / stream_name
        stream_dir.mkdir(parents=True)

        output_pattern = stream_dir / "chunk_%03d.mp3"

        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(recording_path),
            "-af", audio_filter,
            "-ar", "16000",
            "-ac", "1",
            "-c:a", "libmp3lame",
            "-b:a", "64k",
            "-f", "segment",
            "-segment_time", str(chunk_seconds),
            "-segment_list", str(stream_dir / "chunks.csv"),
            "-segment_list_type", "csv",
            "-reset_timestamps", "1",
            str(output_pattern),
        ]

        subprocess.run(command, check=True)

        chunks = sorted(stream_dir.glob("chunk_*.mp3"))

        if not chunks:
            raise RuntimeError(
                f"No chunks created for {stream_name}"
            )

        results[stream_name] = chunks

        print(
            f"{stream_name.upper()}: "
            f"{len(chunks)} chunk(s) created"
        )

    print("\nSeparate-stream processing completed.")

    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: python src/audio_processor.py "
            "<recording.caf>"
        )
        sys.exit(1)

    process_recording(
        sys.argv[1],
        chunk_seconds=300,
    )
