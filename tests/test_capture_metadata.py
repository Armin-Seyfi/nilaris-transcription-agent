import json
import math
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from capture_metadata import capture_path, inspect_pcm_caf, load_capture_metadata, stream_filters


class CaptureMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'nilaris_2026-10-11T21-42-10Z.caf'

    def caf(self, frames=4800, count=3, rate=48000, unknown_length=True):
        desc = struct.pack('>d4sIIIII', rate, b'lpcm', 3, count * 4, 1, count, 32)
        payload = b''.join(struct.pack('<' + 'f' * count,
            *[0.1 * math.sin(2 * math.pi * (300 + channel * 400) * frame / rate)
              for channel in range(count)]) for frame in range(frames))
        self.path.write_bytes(b'caff\x00\x01\x00\x00' + b'desc' + struct.pack('>q',32) + desc
                             + b'data' + struct.pack('>q', -1 if unknown_length else len(payload)+4)
                             + struct.pack('>I', 0) + payload)
        return payload

    def metadata(self, local=(2,), remote=(0, 1), frames=4800, count=3, rate=48000):
        m = {'schema_version':1, 'recording_file':self.path.name, 'status':'completed',
             'channel_count': count, 'sample_rate':rate, 'captured_frames':frames,
             'duration_seconds':frames/rate, 'channels':{'local':list(local), 'remote':list(remote)},
             'input_unit_channel_map':list(range(count)),
             'microphone':{'name':'MacBook Pro Microphone'}, 'blackhole':{'name':'BlackHole 2ch'}}
        capture_path(self.path).write_text(json.dumps(m))
        return m

    def test_reads_raw_pcm_without_ffmpeg_and_supports_unknown_data_size(self):
        self.caf()
        self.assertEqual(inspect_pcm_caf(self.path)['captured_frames'],4800)
        self.caf(unknown_length=False)
        self.assertEqual(inspect_pcm_caf(self.path)['captured_frames'],4800)

    def test_mapping_can_change_between_sessions(self):
        self.caf()
        for local, remote in [((2,), (0,1)), ((0,), (1,2))]:
            self.metadata(local,remote)
            loaded = load_capture_metadata(self.path)
            filters = stream_filters(loaded)
            self.assertIn(f'c{local[0]}',filters['local'])
            self.assertNotIn(f'c{local[0]}',filters['remote'].split('=',2)[-1])

    def test_empty_capture_is_rejected(self):
        self.caf(frames=0); self.metadata(frames=0)
        with self.assertRaisesRegex(ValueError,'no complete audio'):
            load_capture_metadata(self.path)

    def test_historic_audio_is_preserved_and_no_mapping_is_guessed(self):
        self.caf(); before=self.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'no verified capture mapping'):
            load_capture_metadata(self.path)
        self.assertEqual(before,self.path.read_bytes())
        self.assertEqual(list(self.root.iterdir()),[self.path])

    def test_rejects_overlap_out_of_bounds_duplicate_bool_and_wrong_identity(self):
        self.caf()
        for local,remote in [((0,), (0,1)), ((3,), (0,1)), ((2,), (0,0)), ((True,), (0,2))]:
            self.metadata(local,remote)
            with self.assertRaises(ValueError):load_capture_metadata(self.path)
        m=self.metadata();m['input_unit_channel_map']=[1,0,2]
        capture_path(self.path).write_text(json.dumps(m))
        with self.assertRaises(ValueError):load_capture_metadata(self.path)

    def test_rejects_wrong_session_unfinished_capture_frame_and_rate_mismatch(self):
        self.caf()
        for key,val in [('recording_file','another.caf'), ('status','recording'),
                        ('status','failed'), ('captured_frames',4801), ('sample_rate',44100)]:
            m=self.metadata();m[key]=val;capture_path(self.path).write_text(json.dumps(m))
            with self.assertRaises(ValueError):load_capture_metadata(self.path)

    def test_rejects_truncated_pcm(self):
        self.caf();self.path.write_bytes(self.path.read_bytes()[:-1])
        with self.assertRaisesRegex(ValueError,'partial audio frame'):inspect_pcm_caf(self.path)

    def test_metadata_filters_extract_the_correct_channels(self):
        # FFmpeg integration checks real decoded signal, not just filter strings.
        self.caf(frames=48000);m=self.metadata(local=(0,),remote=(1,2),frames=48000)
        from array import array
        for source, expected in [('local',300),('remote',700)]:
            out=subprocess.run(['ffmpeg','-v','error','-i',str(self.path),'-af',stream_filters(m)[source],
                '-f','f32le','-c:a','pcm_f32le','-'],capture_output=True,check=True)
            samples=array('f');samples.frombytes(out.stdout)
            self.assertEqual(len(samples),48000)
            def amplitude(freq):
                return abs(sum(v*complex(math.cos(2*math.pi*freq*i/48000),
                    math.sin(2*math.pi*freq*i/48000)) for i,v in enumerate(samples)))
            self.assertGreater(amplitude(expected),amplitude(1100 if source=='local' else 300)*10)


if __name__=='__main__':unittest.main()
