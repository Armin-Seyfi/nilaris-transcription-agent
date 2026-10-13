"""Validate session-specific capture information without guessing historic layouts."""

import json
import math
import struct
from pathlib import Path


def capture_path(recording):
    return Path(recording).with_suffix('.capture.json')


def inspect_pcm_caf(recording):
    """Read PCM container headers/payload size, never edit the original file."""
    path = Path(recording)
    size = path.stat().st_size
    with path.open('rb') as file:
        if file.read(8) != b'caff\x00\x01\x00\x00':
            raise ValueError('Unsupported CAF header.')
        description = None
        while file.tell() < size:
            header = file.read(12)
            if len(header) != 12:
                raise ValueError('Truncated CAF chunk header.')
            kind, length = struct.unpack('>4sq', header)
            position = file.tell()
            if kind == b'desc':
                if length != 32:
                    raise ValueError('Unsupported CAF description.')
                description = struct.unpack('>d4sIIIII', file.read(32))
            elif kind == b'data':
                if len(file.read(4)) != 4:
                    raise ValueError('Missing CAF edit count.')
                payload = size - file.tell() if length == -1 else length - 4
                if payload <= 0 or payload > size - file.tell():
                    raise ValueError('Recording contains no complete audio payload.')
                if description is None:
                    raise ValueError('CAF description is missing.')
                rate, codec, flags, packet_bytes, packet_frames, channels, bits = description
                if (codec != b'lpcm' or flags != 3 or bits != 32
                        or packet_frames != 1 or packet_bytes != channels * 4):
                    raise ValueError('Unsupported capture PCM format.')
                if not math.isfinite(rate) or rate <= 0 or not 1 <= channels <= 32:
                    raise ValueError('Invalid capture audio format.')
                if payload % packet_bytes:
                    raise ValueError('Recording ends with a partial audio frame.')
                frames = payload // packet_bytes
                return {'sample_rate': rate, 'channel_count': channels,
                        'captured_frames': frames, 'duration_seconds': frames / rate}
            if length < 0 or position + length > size:
                raise ValueError('Invalid CAF chunk length.')
            file.seek(position + length)
    raise ValueError('CAF audio data is missing.')


def validate_mapping(metadata):
    if metadata.get('schema_version') != 1:
        raise ValueError('Unsupported capture metadata version.')
    count = metadata.get('channel_count')
    if type(count) is not int or not 1 <= count <= 32:
        raise ValueError('Invalid capture channel count.')
    sources = metadata.get('channels')
    if not isinstance(sources, dict):
        raise ValueError('Capture channel mapping is missing.')
    combined = []
    for source in ('local', 'remote'):
        indices = sources.get(source)
        if not isinstance(indices, list) or not indices:
            raise ValueError(f'{source}: channel mapping is missing.')
        if any(type(index) is not int or not 0 <= index < count for index in indices):
            raise ValueError(f'{source}: invalid capture channel index.')
        if len(set(indices)) != len(indices):
            raise ValueError(f'{source}: duplicate channel indices.')
        combined.extend(indices)
    if len(set(combined)) != len(combined) or set(combined) != set(range(count)):
        raise ValueError('LOCAL and REMOTE channel mapping overlaps or is incomplete.')
    unit_map = metadata.get('input_unit_channel_map')
    if (not isinstance(unit_map, list) or any(type(i) is not int for i in unit_map)
            or unit_map != list(range(count))):
        raise ValueError('Explicit AudioUnit channel mapping was not verified.')
    return sources


def load_capture_metadata(recording):
    """Fail closed for raw historic recordings; never infer roles from loudness."""
    recording = Path(recording)
    sidecar = capture_path(recording)
    if not sidecar.is_file() or sidecar.is_symlink():
        raise ValueError('This recording has no verified capture mapping. '
                         'Keep existing prepared sessions; historical raw recordings '
                         'need an explicitly verified mapping before preparation.')
    metadata = json.loads(sidecar.read_text(encoding='utf-8'))
    if not isinstance(metadata, dict):
        raise ValueError('Invalid capture metadata.')
    validate_mapping(metadata)
    if metadata.get('recording_file') != recording.name:
        raise ValueError('Capture metadata belongs to a different recording.')
    if metadata.get('status') != 'completed':
        raise ValueError('Recording was not completed safely; original audio is preserved.')
    actual = inspect_pcm_caf(recording)
    if (type(metadata.get('captured_frames')) is not int
            or metadata.get('captured_frames') != actual['captured_frames']
            or metadata.get('channel_count') != actual['channel_count']):
        raise ValueError('Recorded audio does not match capture metadata.')
    rate = metadata.get('sample_rate')
    if (isinstance(rate, bool) or not isinstance(rate, (int, float))
            or not math.isfinite(rate) or abs(rate - actual['sample_rate']) >= 1):
        raise ValueError('Recorded sample rate does not match capture metadata.')
    return metadata


def stream_filters(metadata):
    mapping = validate_mapping(metadata)
    filters = {}
    for source in ('local', 'remote'):
        indices = mapping[source]
        # Equal-weight mono mix; formatting fixed here, never read filter text from JSON.
        expression = '+'.join(f'{1 / len(indices):.12g}*c{index}' for index in indices)
        filters[source] = 'pan=mono|c0=' + expression
        if source == 'local':
            filters[source] += ',highpass=f=100'
    return filters
