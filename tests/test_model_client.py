"""Provider wire contracts, exercised without credentials or network access."""
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from model_client import FirecrawlSearch, ResponsesClient, post_json


class RecordingHTTP:
    def __init__(self, body):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        return io.BytesIO(self.body)


class ResponsesContractTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict('os.environ', {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_responses_request_preserves_structured_tool_conversation(self):
        response = {'id': 'resp_test', 'output': []}
        http = RecordingHTTP(response)
        client = ResponsesClient(api_key='test-placeholder', opener=http)
        schema = {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}
        inputs = [{'role': 'user', 'content': 'What is new?'},
                  {'type': 'function_call', 'call_id': 'call_1', 'name': 'search', 'arguments': '{}'},
                  {'type': 'function_call_output', 'call_id': 'call_1', 'output': '[]'}]
        tools = [{'type': 'function', 'name': 'search', 'parameters': schema, 'strict': True}]
        result = client(stage='assessment', model='configured-model', effort='high',
                        instructions='Clinical instructions', input=inputs, schema=schema, tools=tools)
        self.assertEqual(result, response)
        self.assertEqual(len(http.calls), 1)
        request, timeout = http.calls[0]
        self.assertEqual(request.full_url, 'https://api.openai.com/v1/responses')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-placeholder')
        self.assertEqual(timeout, 40)
        self.assertEqual(json.loads(request.data), {
            'model': 'configured-model', 'store': False, 'max_output_tokens': 5000,
            'instructions': 'Clinical instructions', 'input': inputs,
            'text': {'format': {'type': 'json_schema', 'name': 'assessment', 'strict': True, 'schema': schema}},
            'include': ['reasoning.encrypted_content'], 'reasoning': {'effort': 'high'},
            'tools': tools, 'parallel_tool_calls': False,
        })

    def test_disabled_reasoning_and_no_tools_are_omitted(self):
        for effort in ('none', None, ''):
            with self.subTest(effort=effort):
                http = RecordingHTTP({'output': []})
                ResponsesClient(api_key='test-placeholder', opener=http)(
                    stage='reply', model='configured-model', effort=effort,
                    instructions='', input=[], schema={}, tools=[])
                payload = json.loads(http.calls[0][0].data)
                self.assertNotIn('reasoning', payload)
                self.assertNotIn('tools', payload)
                self.assertNotIn('parallel_tool_calls', payload)

    def test_missing_credentials_and_insecure_endpoint_fail_before_http(self):
        with self.assertRaises(ValueError):
            ResponsesClient()
        with self.assertRaises(ValueError):
            ResponsesClient(api_key='test-placeholder', endpoint='http://example.test/v1/responses')


class FirecrawlContractTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict('os.environ', {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_keyless_v2_search_request_and_markdown_results(self):
        http = RecordingHTTP({'success': True, 'data': {'web': [
            {'url': 'https://nhs.uk/a', 'title': 'A', 'markdown': '# Source', 'description': 'Fallback'},
            {'url': 'https://cdc.gov/b', 'description': 'Summary'},
            {'url': 'https://who.int/c', 'markdown': 'x' * 9000},
            {'url': 'https://nhs.uk/d', 'markdown': 'Must be excluded'},
        ]}})
        result = FirecrawlSearch(opener=http)('fever guidance')
        request, timeout = http.calls[0]
        self.assertEqual(request.full_url, 'https://api.firecrawl.dev/v2/search')
        self.assertEqual(request.get_method(), 'POST')
        self.assertIsNone(request.get_header('Authorization'))
        self.assertEqual(timeout, 20)
        self.assertEqual(json.loads(request.data), {
            'query': 'fever guidance', 'limit': 3, 'sources': ['web'],
            'includeDomains': ['nhs.uk', 'cdc.gov', 'who.int', 'medlineplus.gov', 'nice.org.uk'],
            'scrapeOptions': {'formats': ['markdown']},
        })
        self.assertEqual(result, [
            {'url': 'https://nhs.uk/a', 'title': 'A', 'content': '# Source'},
            {'url': 'https://cdc.gov/b', 'title': 'https://cdc.gov/b', 'content': 'Summary'},
            {'url': 'https://who.int/c', 'title': 'https://who.int/c', 'content': 'x' * 8000},
        ])

    def test_configured_key_and_domains(self):
        http = RecordingHTTP({'success': True, 'data': {'web': []}})
        self.assertEqual(FirecrawlSearch(api_key='firecrawl-placeholder', domains=['nhs.uk'], opener=http)('test'), [])
        request, _ = http.calls[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer firecrawl-placeholder')
        self.assertEqual(json.loads(request.data)['includeDomains'], ['nhs.uk'])

    def test_invalid_rows_are_ignored(self):
        http = RecordingHTTP({'success': True, 'data': {'web': [None, {'url': 7}, {'url': 'https://nhs.uk'}]}})
        self.assertEqual(FirecrawlSearch(opener=http)('test'),
                         [{'url': 'https://nhs.uk', 'title': 'https://nhs.uk', 'content': ''}])

    def test_unsuccessful_provider_response_is_sanitized(self):
        http = RecordingHTTP({'success': False, 'error': 'Provider body with sensitive content'})
        with self.assertRaisesRegex(RuntimeError, 'No current sources') as caught:
            FirecrawlSearch(opener=http)('test')
        self.assertNotIn('sensitive', str(caught.exception))

    def test_malformed_success_envelopes_fail_with_controlled_error(self):
        for data in (None, [], {'web': None}, {'web': 'not an array'}):
            with self.subTest(data=data):
                http = RecordingHTTP({'success': True, 'data': data})
                with self.assertRaises(RuntimeError):
                    FirecrawlSearch(opener=http)('test')


class TransportFailureTests(unittest.TestCase):
    def test_http_error_does_not_expose_key_or_provider_body(self):
        error_body = io.BytesIO(b'private-body')
        def reject(request, *, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, 'private-provider-message',
                                         {'secret-header': 'private-key'}, error_body)
        with self.assertRaisesRegex(RuntimeError, 'HTTP 401') as caught:
            post_json('https://example.test', {}, key='private-key', opener=reject)
        self.assertNotIn('private', str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertTrue(error_body.closed)

    def test_connection_invalid_json_and_size_limits_are_sanitized(self):
        def disconnected(request, *, timeout):
            raise OSError('private connection information')
        for opener in (disconnected, RecordingHTTP(b'private-invalid-json'), RecordingHTTP(b'x' * 1000001)):
            with self.subTest(opener=type(opener).__name__):
                with self.assertRaisesRegex(RuntimeError, 'invalid response') as caught:
                    post_json('https://example.test', {}, opener=opener)
                self.assertNotIn('private', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
