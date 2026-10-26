"""Exercise real HTTP parsing/handlers without binding a sandbox-restricted port."""
import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPResponse
from pathlib import Path
from types import SimpleNamespace

from backends import DemoBackend
from server import handler_for


class StreamSocket:
    def __init__(self, request):
        self.request = request
        self.output = bytearray()

    def makefile(self, *args, **kwargs):
        return io.BytesIO(self.request)

    def sendall(self, data):
        self.output.extend(data)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'web.db'
        self.handler = handler_for(self.path, DemoBackend())

    def request(self, path='/api/state', message=None, cookie=None, origin=None, raw=None):
        body = (json.dumps({'message': message}).encode() if message is not None else b'') if raw is None else raw
        method = 'POST' if message is not None or raw is not None else 'GET'
        headers = [f'{method} {path} HTTP/1.1', 'Host: 127.0.0.1:8000', 'Connection: close']
        if cookie:
            headers.append('Cookie: ' + cookie)
        if origin:
            headers.append('Origin: ' + origin)
        if method == 'POST':
            headers.extend(['Content-Type: application/json', f'Content-Length: {len(body)}'])
        socket = StreamSocket(('\r\n'.join(headers) + '\r\n\r\n').encode() + body)
        self.handler(socket, ('127.0.0.1', 1000), SimpleNamespace(server_port=8000, server_name='localhost'))
        response = HTTPResponse(StreamSocket(bytes(socket.output)))
        response.begin()
        payload = response.read()
        content = json.loads(payload) if response.getheader('Content-Type', '').startswith('application/json') else payload
        cookie = response.getheader('Set-Cookie').split(';')[0]
        return response, content, cookie

    def test_serves_ui_and_empty_family_with_cookie(self):
        response, html, cookie = self.request('/')
        self.assertEqual(response.status, 200)
        self.assertIn(b'Your family', html)
        self.assertIn('HttpOnly', response.getheader('Set-Cookie'))
        response, state, _ = self.request(cookie=cookie)
        self.assertEqual(response.status, 200)
        self.assertEqual(state['members'], [])
        self.assertEqual(state['messages'], [])
        self.assertEqual(state['backend'], 'demo')

    def test_greeting_registration_and_page_refresh_share_saved_session(self):
        _, _, cookie = self.request()
        response, greeting, _ = self.request('/api/chat', 'hi', cookie)
        self.assertEqual((response.status, greeting['route']), (200, 'greeting'))
        _, result, _ = self.request('/api/chat', 'Add my father Raj, age 56', cookie)
        self.assertEqual(result['route'], 'registered')
        _, state, _ = self.request(cookie=cookie)
        self.assertEqual(state['members'][0]['name'], 'Raj')
        self.assertEqual(state['members'][0]['age_months'], 672)
        self.assertEqual([m['role'] for m in state['messages']], ['user', 'assistant', 'user', 'assistant'])
        self.assertEqual(state['messages'][0]['content'], 'hi')

    def test_different_browser_sessions_do_not_share_family_or_messages(self):
        _, _, one = self.request()
        _, _, two = self.request()
        self.request('/api/chat', 'My name is Sachit, I am 27 years old', one)
        _, first, _ = self.request(cookie=one)
        _, second, _ = self.request(cookie=two)
        self.assertEqual(first['members'][0]['name'], 'Sachit')
        self.assertEqual(second['members'], [])
        self.assertEqual(second['messages'], [])

    def test_server_restart_retains_family_and_conversation(self):
        _, _, cookie = self.request()
        self.request('/api/chat', 'Add my mother Sunita, age 51', cookie)
        self.handler = handler_for(self.path, DemoBackend())
        _, state, _ = self.request(cookie=cookie)
        self.assertEqual(state['members'][0]['name'], 'Sunita')
        self.assertEqual(len(state['messages']), 2)

    def test_ui_history_is_not_limited_to_model_context(self):
        _, _, cookie = self.request()
        for _ in range(14):
            self.request('/api/chat', 'hi', cookie)
        _, state, _ = self.request(cookie=cookie)
        self.assertEqual(len(state['messages']), 28)

    def test_clinical_turn_after_registration_uses_real_agent_state(self):
        _, _, cookie = self.request()
        self.request('/api/chat', 'Add my daughter Maya, age 8', cookie)
        _, result, _ = self.request('/api/chat', 'Maya has fever', cookie)
        self.assertEqual(result['route'], 'ask')
        _, result, _ = self.request('/api/chat', 'No', cookie)
        self.assertIn('long', result['message'].lower())
        _, result, _ = self.request('/api/chat', 'No', cookie)
        self.assertIn('hours', result['message'])

    def test_invalid_requests_and_cross_origin_posts_are_rejected(self):
        for raw in (b'{}', b'{', b'[]', b'{"message":123}', b'{"message":""}', b'{"message":"hi","extra":true}', b'x' * 30001):
            with self.subTest(raw=raw[:50]):
                response, result, _ = self.request('/api/chat', raw=raw)
                self.assertEqual(response.status, 400)
                self.assertIn('error', result)
        response, _, _ = self.request('/api/chat', 'hi', origin='https://example.com')
        self.assertEqual(response.status, 403)

    def test_url_paths_do_not_expose_database_or_project_files(self):
        for path in ('/../config.json', '/family_health.db', '/health_agent.py', '/missing'):
            with self.subTest(path=path):
                response, _, _ = self.request(path)
                self.assertEqual(response.status, 404)

    def test_parallel_requests_use_separate_sqlite_connections(self):
        _, _, cookie = self.request()
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: self.request('/api/chat', 'hi', cookie), range(6)))
        self.assertTrue(all(r[0].status == 200 and r[1]['route'] == 'greeting' for r in results))
        _, state, _ = self.request(cookie=cookie)
        self.assertEqual(len(state['messages']), 12)


if __name__ == '__main__':
    unittest.main()
