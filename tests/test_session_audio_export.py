import hashlib
import json
import math
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from media_environment import configure_media_path
configure_media_path()
import session_audio_export as audio

SID = 'nilaris_2026-10-13T12-00-00Z'


class SessionAudioExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.records = self.root/'recordings'
        self.exports = self.root/'audio_exports'
        self.records.mkdir()
        for key,value in [('RECORDINGS_DIR',self.records),('AUDIO_EXPORTS_DIR',self.exports)]:
            p = patch.object(audio,key,value)
            p.start();self.addCleanup(p.stop)
        self.recording = self.records/(SID+'.caf')
        rate = 16000
        desc = struct.pack('>d4sIIIII', rate,b'lpcm',3,12,1,3,32)
        payload = b''.join(struct.pack('<fff',*[0.3*math.sin(2*math.pi*f*n/rate)
                          for f in (400,800,1200)]) for n in range(rate*2))
        self.recording.write_bytes(b'caff\x00\x01\x00\x00'+b'desc'+struct.pack('>q',32)+desc+
            b'data'+struct.pack('>q',-1)+struct.pack('>I',0)+payload)

    def test_legacy_recording_exports_all_channels_without_mapping_or_transcript(self):
        before = hashlib.sha256(self.recording.read_bytes()).hexdigest()
        mp3 = audio.prepare_audio_export(SID)
        result = subprocess.run(['ffmpeg','-v','error','-i',str(mp3),'-ar','16000','-ac','1',
            '-f','f32le','-'],capture_output=True,check=True)
        samples = struct.unpack('<'+'f'*(len(result.stdout)//4),result.stdout)
        self.assertAlmostEqual(len(samples)/16000,2,delta=0.1)
        for f in (400,800,1200):
            amplitude = 2*abs(sum(s*math.sin(2*math.pi*f*n/16000)
                for n,s in enumerate(samples)))/len(samples)
            self.assertGreater(amplitude,0.06)
        self.assertEqual(hashlib.sha256(self.recording.read_bytes()).hexdigest(),before)
        self.assertFalse((self.root/'outputs').exists())

    def test_unchanged_source_reuses_cache_without_conversion(self):
        mp3 = audio.prepare_audio_export(SID)
        with patch.object(audio.subprocess,'run') as convert:
            self.assertEqual(audio.prepare_audio_export(SID),mp3)
            convert.assert_not_called()
        self.recording.write_bytes(self.recording.read_bytes()+b'\x00'*12)
        self.assertIsNone(audio.cached_export(SID))

    def test_missing_corrupt_and_symlinked_audio_fail_without_modifying_original(self):
        with self.assertRaises(ValueError):audio.export_paths('../outside')
        self.recording.write_bytes(b'corrupt audio')
        with self.assertRaises(subprocess.CalledProcessError):audio.prepare_audio_export(SID)
        self.assertEqual(self.recording.read_bytes(),b'corrupt audio')
        self.recording.unlink()
        with self.assertRaises(FileNotFoundError):audio.prepare_audio_export(SID)
        self.recording.symlink_to(self.root/'outside')
        with self.assertRaises(ValueError):audio.prepare_audio_export(SID)

    def test_source_changes_during_conversion_do_not_publish_cache(self):
        fp = audio.fingerprint(self.recording)
        with patch.object(audio,'fingerprint',side_effect=[fp,{**fp,'size':fp['size']+1}]):
            with self.assertRaises(RuntimeError):audio.prepare_audio_export(SID)
        self.assertFalse((self.exports/SID/'session_audio.mp3').exists())
        self.assertEqual(list((self.exports/SID).glob('.audio-*')),[])

    def test_background_preparation_and_download_routes(self):
        import web_server
        manager = audio.AudioExportManager()
        client = web_server.app.test_client()
        base = f'/api/sessions/{SID}'
        with patch.object(web_server,'audio_export_manager',manager), \
             patch.object(web_server.controller,'get_status',return_value={'state':'idle'}):
            self.assertEqual(client.post(base+'/audio/prepare').status_code,403)
            self.assertEqual(client.get(base+'/download/audio').status_code,409)
            response = client.post(base+'/audio/prepare',headers={'Origin':'http://127.0.0.1:8765'})
            self.assertEqual(response.status_code,202)
            deadline=time.monotonic()+5
            while manager.busy() and time.monotonic()<deadline:time.sleep(0.02)
            self.assertEqual(client.get(base+'/audio/status').get_json()['state'],'ready')
            response=client.get(base+'/download/audio')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.mimetype,'audio/mpeg')
            self.assertIn(SID+'_audio.mp3',response.headers['Content-Disposition'])
            self.assertGreater(len(response.data),1000)
            response.close()

    def test_audio_cleanup_and_full_trash_include_export_cache(self):
        import session_storage as storage
        audio.prepare_audio_export(SID)
        outputs=self.root/'outputs'
        folder=outputs/(SID+'_separated');folder.mkdir(parents=True)
        (folder/'transcript.txt').write_text('Preserved transcript')
        (folder/'transcript.json').write_text('{}')
        with patch.object(storage,'RECORDINGS_DIR',self.records), \
             patch.object(storage,'OUTPUTS_DIR',outputs), \
             patch.object(storage,'AUDIO_EXPORTS_DIR',self.exports), \
             patch.object(storage,'_move_all_to_trash',side_effect=lambda paths:paths):
            self.assertIn(self.exports/SID,storage.remove_session_audio(SID))
            self.assertIn(self.exports/SID,storage.trash_entire_session(SID))
        self.assertEqual((folder/'transcript.txt').read_text(),'Preserved transcript')

    def test_storage_changes_are_blocked_during_conversion(self):
        import web_server
        with patch.object(web_server.audio_export_manager,'busy',return_value=True):
            with self.assertRaises(RuntimeError):web_server.ensure_session_storage_available()
