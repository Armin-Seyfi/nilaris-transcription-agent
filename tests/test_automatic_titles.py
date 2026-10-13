import json
import threading
from unittest.mock import patch, MagicMock
import test_session_titles as base
from test_session_titles import SID
import session_titles as titles


class AutomaticTitleTests(base.TitleTests):
    def worker(self, available=lambda: True):
        return titles.AutomaticTitleWorker(self.service, threading.Lock(), available)

    def test_saved_recording_can_be_named_without_creating_prepared_outputs(self):
        sid = 'nilaris_2026-10-13T10-00-00Z'
        (self.records/(sid+'.caf')).write_bytes(b'original audio')
        folder = self.root/(sid+'_separated')
        titles.save_title(sid, 'Interview — Candidate introduction')
        self.assertFalse(folder.exists())
        self.assertEqual(titles.read_title(folder)['title'], 'Interview — Candidate introduction')
        self.assertEqual((self.records/(sid+'.caf')).read_bytes(), b'original audio')

    def test_worker_generates_once_and_restart_does_not_repeat(self):
        with patch('openai.OpenAI') as api:
            api.return_value.chat.completions.create.return_value = self.response()
            self.worker().scan_once()
            self.worker().scan_once()
            api.return_value.chat.completions.create.assert_called_once()
        self.assertEqual(titles.read_title_job(self.folder)['title_state'], 'completed')

    def test_failed_request_is_not_retried_until_requested(self):
        worker = self.worker()
        with patch('openai.OpenAI') as api:
            api.return_value.chat.completions.create.side_effect = TimeoutError('test')
            worker.scan_once()
            worker.scan_once()
            self.assertEqual(api.return_value.chat.completions.create.call_count, 1)
            self.assertEqual(titles.read_title_job(self.folder)['title_state'], 'failed')
            worker.retry(SID)
            api.return_value.chat.completions.create.side_effect = None
            api.return_value.chat.completions.create.return_value = self.response()
            worker.scan_once()
            self.assertEqual(api.return_value.chat.completions.create.call_count, 2)

    def test_busy_recording_and_manual_title_do_not_trigger_ai(self):
        with patch('openai.OpenAI') as api:
            self.worker(lambda:False).scan_once()
            titles.save_title(SID, 'My own title')
            self.worker().scan_once()
            api.assert_not_called()

    def test_interrupted_request_is_not_repeated_after_restart(self):
        titles.write_title_job(self.folder, 'generating')
        with patch('openai.OpenAI') as api:
            self.worker().scan_once()
            api.assert_not_called()
        self.assertEqual(titles.read_title_job(self.folder)['title_state'], 'failed')

    def test_manual_edit_during_network_request_wins(self):
        with patch('openai.OpenAI') as api:
            def response(**kwargs):
                titles.save_title(SID, 'Manual title wins')
                return self.response()
            api.return_value.chat.completions.create.side_effect = response
            self.worker().scan_once()
        self.assertEqual(titles.read_title(self.folder)['title'], 'Manual title wins')

    def test_legacy_titles_are_preserved(self):
        (self.folder/'session_title.json').write_text(json.dumps({'title':'Legacy title', 'source':'manual'}))
        with patch('openai.OpenAI') as api:
            self.worker().scan_once()
            api.assert_not_called()
        titles.save_title(SID, 'Edited title')
        self.assertTrue((self.folder/'session_title.json').exists())
        self.assertEqual(titles.read_title(self.folder)['title'], 'Edited title')

    def test_trashing_session_includes_sidecars_audio_removal_does_not(self):
        import session_storage
        (self.records/(SID+'.caf')).write_bytes(b'audio')
        titles.save_title(SID, 'Title')
        titles.write_title_job(self.folder, 'completed')
        with patch.object(session_storage,'RECORDINGS_DIR',self.records), \
             patch.object(session_storage,'OUTPUTS_DIR',self.root), \
             patch.object(session_storage,'_move_all_to_trash',side_effect=lambda paths:paths):
            audio_paths = session_storage.remove_session_audio(SID)
            trash_paths = session_storage.trash_entire_session(SID)
        self.assertNotIn(titles.sidecar(self.folder), audio_paths)
        self.assertIn(titles.sidecar(self.folder), trash_paths)
        self.assertIn(titles.sidecar(self.folder,'title-job'), trash_paths)
