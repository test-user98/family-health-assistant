import io
import json
import socket
import unittest
import urllib.error
from unittest.mock import patch

from credentials import CredentialError, load_openai_key, openai_key_from_doppler


class CredentialTests(unittest.TestCase):
    def test_retrieves_only_named_openai_secret_and_never_returns_token(self):
        seen = []
        def opener(request, timeout):
            seen.append(request)
            self.assertEqual(timeout, 15)
            return io.BytesIO(json.dumps({'name': 'OPENAI_API_KEY', 'value': {'computed': 'test-openai-value'}}).encode())
        key = openai_key_from_doppler('test-doppler-token', opener=opener)
        self.assertEqual(key, 'test-openai-value')
        self.assertEqual(seen[0].full_url, 'https://api.doppler.com/v3/configs/config/secret?name=OPENAI_API_KEY')
        self.assertEqual(seen[0].get_header('Authorization'), 'Bearer test-doppler-token')

    def test_explicit_or_environment_key_avoids_doppler(self):
        def forbidden(*args, **kwargs):
            self.fail('Doppler must not be queried with an available OpenAI key')
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'env-key', 'DOPPLER_TOKEN': 'test-token'}, clear=True):
            self.assertEqual(load_openai_key(opener=forbidden), 'env-key')
            self.assertEqual(load_openai_key('explicit-key', opener=forbidden), 'explicit-key')

    def test_missing_credentials_returns_none_without_network(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertIsNone(load_openai_key())

    def test_doppler_http_and_network_failures_are_sanitized(self):
        for error in (urllib.error.HTTPError('https://api.doppler.com', 401, 'private-token-in-error', {}, None),
                      urllib.error.URLError('private-token-in-error')):
            with self.subTest(error=type(error).__name__):
                def opener(*args, **kwargs):
                    raise error
                with self.assertRaises(CredentialError) as caught:
                    openai_key_from_doppler('private-token-in-error', opener=opener)
                self.assertNotIn('private-token-in-error', str(caught.exception))

    def test_invalid_or_oversized_response_fails_closed(self):
        for body in (b'[]', b'null', b'{', b'{}', b'{"value":{"computed":5}}', b' ' * 100001):
            with self.subTest(body=body[:50]):
                with self.assertRaises(CredentialError):
                    openai_key_from_doppler('test-token', opener=lambda *a, **k: io.BytesIO(body))

    def test_dns_failure_reports_network_problem_without_disclosing_secrets(self):
        def offline(*args, **kwargs):
            raise urllib.error.URLError(socket.gaierror(8, 'private-token-in-error'))
        with self.assertRaisesRegex(CredentialError, 'resolve api.doppler.com') as caught:
            openai_key_from_doppler('private-token-in-error', opener=offline)
        self.assertNotIn('private-token-in-error', str(caught.exception))

    def test_empty_token_does_not_make_a_request(self):
        with self.assertRaises(CredentialError):
            openai_key_from_doppler(' ')


if __name__ == '__main__':
    unittest.main()
