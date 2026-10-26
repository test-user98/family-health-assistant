import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backends import DemoBackend, OpenAIBackend, output_schema
from health_agent import EXAMPLE
from tests.fixtures import SeededAgent as Agent


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.db'

    def agent(self, backend):
        agent = Agent(self.path, backend)
        self.addCleanup(agent.close)
        return agent

    @staticmethod
    def envelope(data=None, **changes):
        data = data or dict(copy.deepcopy(EXAMPLE), member_id='child', observations=[])
        result = {'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': json.dumps(data)}]}]}
        result.update(changes)
        return result

    def backend(self, envelope, requests=None):
        def opener(request, timeout):
            if requests is not None:
                requests.append((request, timeout))
            return io.BytesIO(json.dumps(envelope).encode())
        return OpenAIBackend('test-not-a-real-key', 'test-model', opener=opener)

    def test_real_http_adapter_request_and_response_contract(self):
        captured = []
        agent = self.agent(self.backend(self.envelope(), captured))
        result = agent.chat('family', 'Maya has fever')
        self.assertEqual(result['route'], 'ask')
        request, timeout = captured[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, 'https://api.openai.com/v1/responses')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-not-a-real-key')
        self.assertFalse(body['store'])
        self.assertTrue(body['text']['format']['strict'])
        self.assertEqual(body['text']['format']['type'], 'json_schema')
        self.assertEqual(timeout, 30)
        self.assertIn('JSON', body['instructions'])
        self.assertEqual(json.loads(body['input'])['message'], 'Maya has fever')
        self.assertEqual(set(output_schema(['fever'])['required']), set(EXAMPLE))

    def test_incomplete_refused_empty_and_malformed_envelopes_fail_closed(self):
        envelopes = [[], None, {}, self.envelope(status='incomplete'), self.envelope(status='failed'),
                     self.envelope(output=[]), self.envelope(output=[None]),
                     self.envelope(output=[{'type': 'message', 'content': [None]}]),
                     self.envelope(output=[{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'no'}]}]),
                     self.envelope(output=[{'type': 'message', 'content': [{'type': 'output_text', 'text': '{'}]}])]
        for envelope in envelopes:
            with self.subTest(envelope=envelope):
                agent = self.agent(self.backend(envelope))
                self.assertEqual(agent.chat('family', 'Maya fever')['route'], 'error')
                self.assertEqual(agent.state('family')['episodes'], {})

    def test_timeout_and_invalid_utf8_fail_closed(self):
        def timeout(*args, **kwargs):
            raise TimeoutError('example')
        def invalid_utf8(*args, **kwargs):
            return io.BytesIO(b'\xff')
        for opener in (timeout, invalid_utf8):
            with self.subTest(opener=opener):
                agent = self.agent(OpenAIBackend('test', 'test', opener=opener))
                self.assertEqual(agent.chat('family', 'Maya fever')['route'], 'error')

    def test_insecure_endpoint_is_rejected(self):
        with self.assertRaises(ValueError):
            OpenAIBackend('test', 'test', endpoint='http://example.com')

    def test_large_provider_response_fails_closed(self):
        backend = OpenAIBackend('test', 'test', opener=lambda *a, **k: io.BytesIO(b' ' * 200001))
        self.assertEqual(self.agent(backend).chat('family', 'Maya fever')['route'], 'error')

    def test_yes_to_emergency_question_escalates_without_more_questions(self):
        agent = self.agent(DemoBackend())
        self.assertEqual(agent.chat('family', 'Maya has fever')['route'], 'ask')
        self.assertEqual(agent.chat('family', 'Yes')['route'], 'emergency')

    def test_plain_no_and_yes_answers_complete_focused_intake(self):
        agent = self.agent(DemoBackend())
        for message in ('Maya fever', 'No', 'For 12 hours', 'Mild'):
            self.assertEqual(agent.chat('family', message)['route'], 'ask')
        self.assertEqual(agent.chat('family', 'Yes')['route'], 'home')

    def test_elapsed_duration_crosses_home_monitoring_boundary(self):
        clock = [100000.0]
        agent = Agent(self.path, DemoBackend(), clock=lambda: clock[0])
        self.addCleanup(agent.close)
        self.assertEqual(agent.chat('family', 'Maya has a mild cough for 47 hours, no red flags, drinking and urinating normally')['route'], 'home')
        clock[0] += 2 * 3600
        self.assertEqual(agent.chat('family', 'She still has mild symptoms')['route'], 'doctor')

    def test_very_high_temperature_cannot_receive_home_monitoring(self):
        agent = self.agent(DemoBackend())
        result = agent.chat('family', 'Maya has mild fever at 41 C for 2 hours, no red flags, drinking and urinating normally')
        self.assertEqual(result['route'], 'doctor')
        self.assertIn('very_high_temperature', result['reasons'])

    def test_conditions_outside_topic_examples_remain_distinct(self):
        agent = self.agent(DemoBackend())
        first = agent.chat('family', 'Maya has knee pain')
        second = agent.chat('family', 'Maya has tooth pain')
        self.assertEqual((first['route'], second['route']), ('ask', 'ask'))
        self.assertNotEqual(first['episode_id'], second['episode_id'])
        self.assertEqual({e['topic'] for e in agent.state('family')['episodes'].values()}, {'knee pain', 'tooth pain'})

    def test_cli_single_turn_returns_json_without_dependencies(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, str(root / 'health_agent.py'), '--backend', 'demo',
                                 '--db', str(self.path), '--message', 'Maya has fever'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['route'], 'clarify_member')

    def test_cli_missing_credentials_does_not_silently_use_demo(self):
        root = Path(__file__).resolve().parents[1]
        env = {k: v for k, v in os.environ.items() if k not in ('OPENAI_API_KEY', 'HEALTH_MODEL')}
        result = subprocess.run([sys.executable, str(root / 'health_agent.py'), '--db', str(self.path), '--message', 'Maya fever'],
                                capture_output=True, text=True, timeout=10, env=env)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Set OPENAI_API_KEY or DOPPLER_TOKEN', result.stderr)
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
