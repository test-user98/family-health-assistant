"""HTTP concurrency and lightweight sidebar refreshes, without a server port."""
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import patch

import server
from tests.test_server import WebTests


class BlockingSettings:
    def __init__(self):
        self.blocked, self.release, self.following = (threading.Event() for _ in range(3))

    def metadata(self, session):
        return {'configured': True, 'models': {}}

    def engine(self, session, db_path):
        settings = self

        class Engine:
            def chat(self, session, message):
                if message == 'blocked':
                    settings.blocked.set()
                    if not settings.release.wait(3):
                        raise TimeoutError('Test did not release the first turn')
                else:
                    settings.following.set()
                return {'route': 'general', 'message': message + ' reply',
                        'member_id': None, 'episode_id': None, 'trace': {}, 'sources': []}

            def close(self):
                pass

        return Engine()


class ChatTransportTests(unittest.TestCase):
    def setUp(self):
        self.web = WebTests('test_serves_ui_and_empty_family_with_cookie')
        self.web.setUp()
        self.addCleanup(self.web.doCleanups)

    def test_slow_model_for_one_household_does_not_block_another(self):
        settings = BlockingSettings()
        self.web.handler = server.handler_for(self.web.path, settings=settings)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.web.request, '/api/chat', 'blocked', 'family_session=' + 'a' * 32)
            try:
                self.assertTrue(settings.blocked.wait(1))
                second = pool.submit(self.web.request, '/api/chat', 'following', 'family_session=' + 'b' * 32)
                self.assertTrue(settings.following.wait(1), 'A different household waited behind a model call')
                self.assertEqual(second.result(timeout=1)[0].status, 200)
            finally:
                settings.release.set()
            self.assertEqual(first.result(timeout=1)[0].status, 200)

    def test_same_household_still_commits_turns_in_order(self):
        settings = BlockingSettings()
        self.web.handler = server.handler_for(self.web.path, settings=settings)
        cookie = 'family_session=' + 'a' * 32
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.web.request, '/api/chat', 'blocked', cookie)
            try:
                self.assertTrue(settings.blocked.wait(1))
                second = pool.submit(self.web.request, '/api/chat', 'following', cookie)
                self.assertFalse(settings.following.wait(.1), 'Same-household turns ran concurrently')
            finally:
                settings.release.set()
            self.assertEqual(first.result(timeout=1)[0].status, 200)
            self.assertEqual(second.result(timeout=1)[0].status, 200)
        _, state, _ = self.web.request(cookie=cookie)
        self.assertEqual([m['content'] for m in state['messages']],
                         ['blocked', 'blocked reply', 'following', 'following reply'])

    def test_sidebar_refresh_returns_members_without_querying_chat_history(self):
        _, _, cookie = self.web.request()
        self.web.request('/api/chat', 'Add my father Raj, age 56', cookie)
        statements = []
        original = server.database

        @contextmanager
        def traced(path):
            with original(path) as db:
                db.set_trace_callback(statements.append)
                yield db

        with patch('server.database', traced):
            response, data, _ = self.web.request('/api/state?history=0', cookie=cookie)
        self.assertEqual(response.status, 200)
        self.assertEqual(data['members'][0]['name'], 'Raj')
        self.assertNotIn('messages', data)
        self.assertFalse(any('from chat_messages' in statement.lower() for statement in statements))
        _, normal, _ = self.web.request(cookie=cookie)
        self.assertEqual(len(normal['messages']), 2)


if __name__ == '__main__':
    unittest.main()
