"""Conservative segment-boundary repair. Not VAD or word-level forced alignment."""
import math
import re
import subprocess
from pathlib import Path

NOISE_THRESHOLD_DB = -70
MIN_SILENCE_SECONDS = 1.0
ONSET_MARGIN_SECONDS = 0.05
_EVENT = re.compile(r'silence_(start|end):\s*([-+\d.eE]+)')


def audio_timing(audio_path):
    """Read decoded audio duration and long near-digital silence intervals."""
    path = Path(audio_path)
    probe = subprocess.run([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', str(path)
    ], capture_output=True, text=True, check=True, timeout=30)
    duration = float(probe.stdout.strip())
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f'Invalid audio duration: {path.name}')
    result = subprocess.run([
        'ffmpeg', '-nostdin', '-hide_banner', '-nostats', '-i', str(path),
        '-af', f'silencedetect=noise={NOISE_THRESHOLD_DB}dB:d={MIN_SILENCE_SECONDS}',
        '-f', 'null', '-'
    ], capture_output=True, text=True, check=True, timeout=max(60, duration * 2))
    intervals = []
    start = None
    for kind, text in _EVENT.findall(result.stderr):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError('Invalid silence detection result.')
        if kind == 'start':
            start = max(0.0, value)
        elif start is not None:
            end = min(duration, value)
            if end > start:
                intervals.append((start, end))
            start = None
    if start is not None and start < duration:
        intervals.append((start, duration))
    return duration, intervals


def finite_seconds(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Segment timestamps must be finite numbers.')
    return float(value)


def repair_segment(start, end, *, duration, silences):
    """Keep raw times in provenance; never move an entire transcript or edit text."""
    start = finite_seconds(start)
    end = finite_seconds(end)
    if start < 0 or end < start or not math.isfinite(duration) or duration <= 0:
        raise ValueError('Invalid segment timing.')
    raw = {'start': start, 'end': end}
    reasons = []
    reviews = []
    # Do not invent an alignment for text placed completely outside recorded audio.
    if start >= duration:
        raise ValueError('Nonempty segment starts outside recorded audio; review required.')
    if end > duration:
        end = duration
        reasons.append('end_clipped_to_recorded_audio')
    for quiet_start, quiet_end in silences:
        if quiet_start - 0.001 <= start < quiet_end:
            if quiet_end - start < MIN_SILENCE_SECONDS:
                break
            if quiet_end >= end:
                reviews.append('segment_inside_near_silence_alignment_unknown')
            else:
                candidate = max(start, quiet_end - ONSET_MARGIN_SECONDS)
                if candidate < end:
                    start = candidate
                    reasons.append('long_near_silence_trimmed_from_segment_start')
            break
    if end < start:
        raise ValueError('Timestamp repair produced an invalid segment.')
    provenance = {'raw_start': raw['start'], 'raw_end': raw['end'],
                  'method': 'conservative_near_silence_boundary_v1',
                  'adjustments': reasons, 'review_flags': reviews,
                  'word_alignment_verified': False}
    return start, end, provenance
