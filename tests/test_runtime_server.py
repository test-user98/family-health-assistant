"""Real runtime/settings HTTP integration without external provider requests."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from backends import DemoBackend
from server import RuntimeSettings, database, handler_for
from tests.test_runtime import envelope, intake
from tests import test_server


class FakeClient:
    calls = []
    fail_model = None

    def __init__(self, api_key=None):
        self.key = api_key

    def __call__(self, **kwargs):
        type(self).calls.append(copy.deepcopy(kwargs))
        if kwargs['model'] == type(self).fail_model:
            raise RuntimeError('Do not expose provider bodies or test-secret here.')
        if kwargs['stage'] == 'connection_test':
            return envelope({'ok': True})
        if kwargs['stage'] == 'intake':
            return envelope(intake())
        return envelope({'message': 'Hi! How can I help your family?', 'source_ids': []})


class RuntimeServerTests(unittest.TestCase):
    def setUp(self):
        self.web = test_server.WebTests('test_serves_ui_and_empty_family_with_cookie')
        self.web.setUp()
        self.addCleanup(self.web.doCleanups)
        self.path = self.web.path
        self.settings = RuntimeSettings(load_credentials=False)
        self.web.handler = handler_for(self.path, settings=self.settings)
        self.client_patch = patch('server.ResponsesClient', FakeClient)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        FakeClient.calls, FakeClient.fail_model = [], None

    def request(self, *args, **kwargs):
        return self.web.request(*args, **kwargs)

    @staticmethod
    def payload(**changes):
        result = {'conversation_model': 'conversation-test', 'conversation_effort': 'low',
                  'assessment_model': 'assessment-test', 'assessment_effort': 'medium',
                  'openai_api_key': 'test-secret'}
        result.update(changes)
        return result

    def configure(self, payload, cookie):
        return self.request('/api/settings', cookie=cookie, raw=json.dumps(payload).encode())

    def test_default_has_no_regex_fallback_when_model_is_not_connected(self):
        _, state, cookie = self.request()
        self.assertEqual(state['backend'], 'llm')
        self.assertFalse(state['runtime']['configured'])
        self.assertTrue(state['runtime']['web_search']['configured'])
        self.assertFalse(state['runtime']['web_search']['authenticated'])
        response, result, _ = self.request('/api/chat', 'Can you help me?', cookie)
        self.assertEqual(response.status, 200)
        self.assertEqual(result['route'], 'setup_required')
        self.assertEqual(result['trace']['calls'], [])
        self.assertEqual(FakeClient.calls, [])

    def test_explicit_demo_does_not_load_doppler_or_provider_credentials(self):
        with patch('server.load_openai_key', side_effect=AssertionError('Unexpected credential access')):
            self.web.handler = handler_for(self.path, DemoBackend())
        _, state, _ = self.request()
        self.assertEqual(state['backend'], 'demo')

    def test_saving_probes_both_profiles_and_never_returns_or_persists_keys(self):
        _, _, cookie = self.request()
        response, result, _ = self.configure(self.payload(), cookie)
        self.assertEqual(response.status, 200)
        self.assertTrue(result['runtime']['configured'])
        self.assertEqual([(c['model'], c['effort']) for c in FakeClient.calls],
                         [('conversation-test', 'low'), ('assessment-test', 'medium')])
        self.assertTrue(all(c['stage'] == 'connection_test' for c in FakeClient.calls))
        self.assertNotIn('test-secret', json.dumps(result))
        self.assertNotIn(b'test-secret', self.path.read_bytes())

    def test_bad_effort_and_model_id_do_not_call_provider(self):
        _, _, cookie = self.request()
        for changes in ({'conversation_effort': 'anything'}, {'assessment_model': 'spaces not allowed'},
                        {'openai_api_key': 123}, {'extra': True}):
            with self.subTest(changes=changes):
                response, _, _ = self.configure(self.payload(**changes), cookie)
                self.assertEqual(response.status, 400)
        self.assertEqual(FakeClient.calls, [])

    def test_second_profile_failure_is_sanitized_and_does_not_commit(self):
        _, _, cookie = self.request()
        self.configure(self.payload(), cookie)
        _, before, _ = self.request(cookie=cookie)
        FakeClient.fail_model = 'failing-assessment'
        response, failed, _ = self.configure(self.payload(conversation_model='new-model', assessment_model='failing-assessment'), cookie)
        self.assertEqual(response.status, 400)
        self.assertIn('assessment', failed['error'])
        self.assertNotIn('test-secret', json.dumps(failed))
        _, after, _ = self.request(cookie=cookie)
        self.assertEqual(before['runtime'], after['runtime'])

    def test_settings_and_credentials_are_isolated_by_browser_session(self):
        _, _, one = self.request()
        _, _, two = self.request()
        self.configure(self.payload(), one)
        _, first, _ = self.request(cookie=one)
        _, second, _ = self.request(cookie=two)
        self.assertTrue(first['runtime']['configured'])
        self.assertFalse(second['runtime']['configured'])
        restarted = RuntimeSettings(load_credentials=False)
        self.assertFalse(restarted.metadata(one.split('=')[1])['configured'])

    def test_actual_runtime_calls_and_trace_survive_history_refresh(self):
        _, _, cookie = self.request()
        self.configure(self.payload(), cookie)
        response, answer, _ = self.request('/api/chat', 'Can you help me?', cookie)
        self.assertEqual(response.status, 200)
        self.assertEqual(answer['message'], 'Hi! How can I help your family?')
        self.assertEqual(len(answer['trace']['calls']), 2)
        _, state, _ = self.request(cookie=cookie)
        self.assertEqual(state['messages'][-1]['trace'], answer['trace'])
        self.assertEqual(state['messages'][-1]['sources'], [])
        self.assertNotIn('test-secret', json.dumps(state))
        self.assertNotIn(b'test-secret', self.path.read_bytes())

    def test_legacy_message_table_migrates_without_erasing_history(self):
        old = Path(self.web.temp.name) / 'old.db'
        with database(old) as db:
            db.execute('CREATE TABLE chat_messages(id INTEGER PRIMARY KEY, session TEXT, role TEXT, content TEXT, at REAL)')
            db.execute('INSERT INTO chat_messages VALUES (1, ?, ?, ?, 1)', ('a' * 32, 'assistant', 'Saved older reply'))
        self.web.handler = handler_for(old, settings=self.settings)
        _, state, _ = self.request(cookie='family_session=' + 'a' * 32)
        self.assertEqual(state['messages'][0]['content'], 'Saved older reply')
        with database(old) as db:
            self.assertIn('metadata', {r[1] for r in db.execute('PRAGMA table_info(chat_messages)')})

    def test_doppler_token_is_consumed_only_for_named_openai_key(self):
        _, _, cookie = self.request()
        payload = self.payload(doppler_token='scoped-doppler-token')
        payload.pop('openai_api_key')
        with patch('server.openai_key_from_doppler', return_value='loaded-provider-key') as loader:
            response, result, _ = self.configure(payload, cookie)
        loader.assert_called_once_with('scoped-doppler-token')
        self.assertEqual(response.status, 200)
        for secret in ('scoped-doppler-token', 'loaded-provider-key'):
            self.assertNotIn(secret, json.dumps(result))
            self.assertNotIn(secret.encode(), self.path.read_bytes())

    def test_search_is_available_without_a_key_and_optional_key_is_probed(self):
        runtime = self.settings.engine('family', self.path)
        self.addCleanup(runtime.close)
        self.assertIsNotNone(runtime.search)
        _, _, cookie = self.request()
        with patch('server.FirecrawlSearch') as search:
            search.return_value.return_value = []
            response, result, _ = self.configure(self.payload(firecrawl_api_key='search-secret'), cookie)
        self.assertEqual(response.status, 200)
        self.assertTrue(result['runtime']['web_search']['authenticated'])
        search.assert_called_once_with(api_key='search-secret')
        search.return_value.assert_called_once()
        self.assertNotIn('search-secret', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
