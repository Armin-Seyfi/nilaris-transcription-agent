"""On-demand local MP3 exports from original captures, including historical CAFs."""
import json
import math
import os
import subprocess
import tempfile
import threading

from app_paths import RECORDINGS_DIR, AUDIO_EXPORTS_DIR
from session_storage import SESSION_ID_PATTERN, _check_safe_path

FORMAT_VERSION = 'all-channels-mono-128k-44100-v1'


def export_paths(session_id):
    if not isinstance(session_id, str) or not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError('Invalid session ID.')
    recording = RECORDINGS_DIR / (session_id + '.caf')
    folder = AUDIO_EXPORTS_DIR / session_id
    audio = folder / 'session_audio.mp3'
    metadata = folder / 'export.json'
    _check_safe_path(recording, RECORDINGS_DIR)
    for path in (folder, audio, metadata):
        _check_safe_path(path, AUDIO_EXPORTS_DIR)
    return recording, folder, audio, metadata


def fingerprint(recording):
    stat = recording.stat()
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            'ctime_ns': stat.st_ctime_ns, 'inode': stat.st_ino}


def cached_export(session_id):
    recording, _, audio, metadata = export_paths(session_id)
    if not recording.is_file():
        raise FileNotFoundError('Original session audio is no longer available.')
    try:
        data = json.loads(metadata.read_text(encoding='utf-8'))
        if (data['format'] == FORMAT_VERSION and data['source'] == fingerprint(recording)
                and audio.is_file() and audio.stat().st_size == data['export_size']
                and audio.stat().st_size > 0):
            return audio
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def prepare_audio_export(session_id):
    recording, folder, audio, metadata = export_paths(session_id)
    if not recording.is_file():
        raise FileNotFoundError('Original session audio is no longer available.')
    cached = cached_export(session_id)
    if cached is not None:
        return cached
    before = fingerprint(recording)
    probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
        '-show_entries', 'stream=channels', '-of', 'json', str(recording)],
        capture_output=True, text=True, check=True, timeout=30)
    try:
        channels = json.loads(probe.stdout)['streams'][0]['channels']
    except (ValueError, KeyError, IndexError, TypeError):
        raise ValueError('Recording contains no readable audio.') from None
    if type(channels) is not int or not 1 <= channels <= 32:
        raise ValueError('Unsupported recording channel count.')
    # Include every recorded channel explicitly, avoiding layout-dependent default
    # downmixes and any guess about historical microphone/system channel roles.
    expression = '+'.join(f'{1/channels:.12g}*c{i}' for i in range(channels))
    folder.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.audio-', suffix='.mp3', dir=folder)
    os.close(fd)
    try:
        subprocess.run(['ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'error',
            '-i', str(recording), '-map', '0:a:0', '-vn', '-map_metadata', '-1',
            '-af', 'pan=mono|c0='+expression, '-ar', '44100', '-ac', '1',
            '-c:a', 'libmp3lame', '-b:a', '128k', temporary],
            capture_output=True, text=True, check=True, timeout=3600)
        check = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', temporary],
            capture_output=True, text=True, check=True, timeout=30)
        duration = float(check.stdout.strip())
        if not math.isfinite(duration) or duration <= 0 or os.path.getsize(temporary) <= 0:
            raise ValueError('Recording contains no readable audio.')
        if fingerprint(recording) != before:
            raise RuntimeError('Recording changed during export. Try again after recording stops.')
        os.replace(temporary, audio)
        fd, temp_meta = tempfile.mkstemp(prefix='.export-', dir=folder)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as file:
                json.dump({'format': FORMAT_VERSION, 'source': before,
                    'export_size': audio.stat().st_size, 'channels_mixed': channels}, file)
            os.replace(temp_meta, metadata)
        finally:
            if os.path.exists(temp_meta):
                os.unlink(temp_meta)
        return audio
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class AudioExportManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {}

    def busy(self):
        with self.lock:
            return any(job['state'] == 'preparing' for job in self.jobs.values())

    def prepare(self, session_id):
        recording, _, _, _ = export_paths(session_id)
        if not recording.is_file():
            raise FileNotFoundError('Original session audio is no longer available.')
        with self.lock:
            if any(job['state'] == 'preparing' for job in self.jobs.values()):
                existing = self.jobs.get(session_id)
                if existing and existing['state'] == 'preparing':
                    return dict(existing)
                raise RuntimeError('Another audio download is being prepared. Please wait.')
            self.jobs[session_id] = {'session_id': session_id, 'state': 'preparing', 'error': None}
        thread = threading.Thread(target=self._run, args=(session_id,), daemon=True)
        try:
            thread.start()
        except Exception:
            with self.lock:
                self.jobs[session_id]['state'] = 'failed'
                self.jobs[session_id]['error'] = 'Could not start audio export.'
            raise
        return self.status(session_id)

    def _run(self, session_id):
        try:
            prepare_audio_export(session_id)
            result = {'session_id': session_id, 'state': 'ready', 'error': None}
        except Exception as error:
            if isinstance(error, subprocess.CalledProcessError):
                message = 'Could not decode session audio. The original file was preserved.'
            elif isinstance(error, subprocess.TimeoutExpired):
                message = 'Audio preparation timed out. The original file was preserved.'
            else:
                message = str(error)
            result = {'session_id': session_id, 'state': 'failed', 'error': message}
        with self.lock:
            self.jobs[session_id] = result

    def status(self, session_id):
        export_paths(session_id)
        with self.lock:
            return dict(self.jobs.get(session_id, {'session_id': session_id, 'state': 'idle', 'error': None}))
