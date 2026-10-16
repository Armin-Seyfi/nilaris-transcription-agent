import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from recording_controller import RecordingController


class FakeProcess:
    def __init__(self):
        self.stdin=io.StringIO();self.pid=123;self.returncode=None
    def poll(self):return self.returncode
    def wait(self,timeout=None):self.returncode=0;return 0


class RecordingValidationTests(unittest.TestCase):
    def test_successful_process_exit_cannot_mark_header_only_file_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);file=root/'nilaris_2026-10-11T21-11-16Z.caf'
            file.write_bytes(b'header only')
            c=RecordingController();c.process=FakeProcess();c.ready_event.set()
            with patch('recording_controller.RECORDINGS_DIR',root),patch.object(c,'_restore_audio_route'):
                with self.assertRaisesRegex(ValueError,'no verified capture mapping'):
                    c.stop_recording()
            self.assertTrue(file.exists())
            self.assertIsNone(c.last_completed_recording)
            self.assertEqual(c.get_status()['state'],'error')

    def test_stdout_reports_auto_selected_device(self):
        c=RecordingController()
        class Output:
            stdout=['NILARIS_OUTPUT: WH-1000XM4\n','NILARIS_READY\n']
        c._read_output(Output())
        self.assertEqual(c.listening_device,'WH-1000XM4')
        self.assertTrue(c.ready_event.is_set())


    def test_disconnect_warning_and_new_listener_are_visible_while_recording(self):
        c=RecordingController();c.process=FakeProcess();c.ready_event.set()
        class Output:
            stdout=['NILARIS_OUTPUT: WH-1000XM4\n',
                    'NILARIS_ROUTE_WARNING: Reconnecting audio; a short gap will be marked\n',
                    'NILARIS_OUTPUT: MacBook Pro Speakers\n',
                    'NILARIS_ROUTE_WARNING: Capture continued; now listening through MacBook Pro Speakers. Brief remote-audio interruption is possible.\n']
        c._read_output(Output())
        status=c.get_status()
        self.assertEqual(status['state'],'recording')
        self.assertEqual(status['listening_device'],'MacBook Pro Speakers')
        self.assertIn('Capture continued',status['route_warning'])


if __name__=='__main__':unittest.main()
