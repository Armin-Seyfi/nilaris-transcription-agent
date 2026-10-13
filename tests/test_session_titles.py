import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import session_titles as titles

SID = 'nilaris_2026-10-12T07-09-39Z'


class TitleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root/(SID+'_separated')
        self.folder.mkdir()
        self.transcript = {'segments': [{'text': 'درس ارزیابی مدل‌های طبقه‌بندی در Ironhack.'}]}
        (self.folder/'transcript.json').write_text(json.dumps(self.transcript, ensure_ascii=False))
        (self.folder/'transcript.txt').write_text('original transcript')
        self.patch = patch.object(titles, 'OUTPUTS_DIR', self.root)
        self.patch.start()
        self.records = self.root/'recordings'
        self.records.mkdir()
        self.record_patch = patch.object(titles, 'RECORDINGS_DIR', self.records)
        self.record_patch.start()
        self.service = titles.TitleService()

    def tearDown(self):
        self.record_patch.stop()
        self.patch.stop()
        self.temp.cleanup()

    def response(self):
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
            refusal=None, content=json.dumps({'kind':'class', 'type_label':'کلاس',
            'topic':'ارزیابی مدل‌های طبقه‌بندی', 'context':'Ironhack'})))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50))

    def test_manual_edit_preserves_transcript_and_keeps_previous_title(self):
        before = (self.folder/'transcript.json').read_bytes()
        titles.save_title(SID, 'کلاس — ارزیابی مدل')
        titles.save_title(SID, 'کلاس — طبقه‌بندی | Ironhack')
        self.assertEqual((self.folder/'transcript.json').read_bytes(), before)
        self.assertEqual((self.folder/'transcript.txt').read_text(), 'original transcript')
        self.assertEqual(titles.read_title(self.folder)['title'], 'کلاس — طبقه‌بندی | Ironhack')
        self.assertEqual(json.loads((titles.sidecar(self.folder)).read_text())['history'][0]['title'], 'کلاس — ارزیابی مدل')

    def test_quote_and_manual_edit_never_call_openai(self):
        with patch('openai.OpenAI') as api:
            q = self.service.quote(SID)
            titles.save_title(SID, 'My title')
            self.assertFalse(q['api_request_sent'])
            self.assertGreater(q['estimated_cost_usd'], 0)
            api.assert_not_called()

    def test_explicit_one_time_approval_generates_and_persists_title(self):
        q = self.service.quote(SID)
        with patch('openai.OpenAI') as api:
            api.return_value.chat.completions.create.return_value = self.response()
            result = self.service.generate(SID, q['approval_token'], True)
            self.assertEqual(result['title'], 'کلاس — ارزیابی مدل‌های طبقه‌بندی | Ironhack')
            api.assert_called_once_with(timeout=35, max_retries=0)
            with self.assertRaises(ValueError):
                self.service.generate(SID, q['approval_token'], True)
            api.return_value.chat.completions.create.assert_called_once()

    def test_missing_approval_expired_quote_and_wrong_session_cannot_call_api(self):
        with patch('openai.OpenAI') as api:
            q = self.service.quote(SID)
            with self.assertRaises(ValueError):
                self.service.generate(SID, q['approval_token'], False)
            self.service.quotes[q['approval_token']]['expires'] = 0
            with self.assertRaises(ValueError):
                self.service.generate(SID, q['approval_token'], True)
            q = self.service.quote(SID)
            with self.assertRaises(ValueError):
                self.service.generate('nilaris_2026-10-11T07-09-39Z', q['approval_token'], True)
            api.assert_not_called()

    def test_changed_transcript_or_manual_title_invalidates_quote(self):
        for change in ('transcript', 'title'):
            q = self.service.quote(SID)
            if change == 'transcript':
                (self.folder/'transcript.json').write_text(json.dumps({'segments':[{'text':'New subject'}]}))
            else:
                titles.save_title(SID, 'Manual correction')
            with patch('openai.OpenAI') as api:
                with self.assertRaises(RuntimeError):
                    self.service.generate(SID, q['approval_token'], True)
                api.assert_not_called()

    def test_timeout_has_no_retry_and_preserves_manual_title(self):
        titles.save_title(SID, 'Preserve me')
        q = self.service.quote(SID)
        with patch('openai.OpenAI') as api:
            api.return_value.chat.completions.create.side_effect = TimeoutError('timeout')
            with self.assertRaises(TimeoutError):
                self.service.generate(SID, q['approval_token'], True)
            api.return_value.chat.completions.create.assert_called_once()
        self.assertEqual(titles.read_title(self.folder)['title'], 'Preserve me')

    def test_invalid_path_symlink_and_incomplete_transcript_rejected(self):
        with self.assertRaises(ValueError):
            titles.session_folder('../../outside')
        (titles.sidecar(self.folder)).symlink_to(self.root/'outside')
        with self.assertRaises(ValueError):
            titles.save_title(SID, 'Title')
        (self.records/(SID+'.title.json')).unlink()
        (self.folder/'transcript.txt').unlink()
        with self.assertRaises(FileNotFoundError):
            self.service.quote(SID)

    def test_empty_long_and_control_titles_rejected_zwnj_preserved(self):
        for value in ('', 'a'*161, 'a\nb', 'a\u202eb', None):
            with self.assertRaises(ValueError):
                titles.clean_title(value)
        self.assertEqual(titles.clean_title('مدل‌های زبانی'), 'مدل‌های زبانی')

    def test_sampling_spans_whole_transcript_with_bounded_payload(self):
        (self.folder/'transcript.json').write_text(json.dumps({'segments':[{'text':'START '+ 'x'*100000+' END'}]}))
        payload, _, sampled = titles.transcript_input(self.folder)
        self.assertTrue(sampled)
        text = json.loads(payload)['transcript']
        self.assertTrue(text.startswith('START'))
        self.assertTrue(text.endswith('END'))
        self.assertLess(len(text), 25000)

    def test_history_includes_title_after_audio_removal(self):
        import session_history
        titles.save_title(SID, 'Class — Evaluation')
        (self.folder/'session_metadata.json').write_text('{"duration_seconds": 30}')
        with patch.object(session_history, 'OUTPUTS_DIR', self.root), \
             patch.object(session_history, 'RECORDINGS_DIR', self.root/'no_audio'):
            session = session_history.list_sessions()[0]
        self.assertEqual(session['title'], 'Class — Evaluation')
        self.assertFalse(session['has_audio'])
        self.assertTrue(session['has_transcript'])

    def test_routes_require_origin_and_explicit_approval(self):
        import web_server
        client = web_server.app.test_client()
        url = f'/api/sessions/{SID}/title'
        self.assertEqual(client.post(url, json={'title':'Title'}).status_code, 403)
        self.assertEqual(client.get(url).status_code, 405)
        headers = {'Origin':'http://127.0.0.1:8765'}
        with patch.object(web_server, 'ensure_session_storage_available'):
            self.assertEqual(client.post(url, json={'title':'Title'}, headers=headers).status_code, 200)
            with patch('openai.OpenAI') as api:
                response = client.post(url+'/retry', json={}, headers=headers)
                self.assertEqual(response.status_code, 400)  # Manual title already exists.
                api.assert_not_called()



if __name__ == '__main__':
    unittest.main()
