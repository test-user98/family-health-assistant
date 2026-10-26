"""Bounded JSON HTTP clients; no secret is written to disk or included in errors."""
import json
import os
import time
import urllib.error
import urllib.request


def post_json(url, payload, key=None, timeout=40, opener=None):
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    request = urllib.request.Request(url, json.dumps(payload).encode(), headers)
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            raw = response.read(1000001)
        if len(raw) > 1000000:
            raise ValueError('Provider response exceeded the size limit.')
        return json.loads(raw)
    except urllib.error.HTTPError as error:
        # Never copy provider bodies/headers into logs: they can contain sensitive values.
        status = error.code
        error.close()
        raise RuntimeError(f'Provider returned HTTP {status}. Check credentials, model access, and supported effort.') from None
    except (OSError, ValueError):
        raise RuntimeError('Provider connection failed or returned an invalid response.') from None


class ResponsesClient:
    def __init__(self, api_key=None, endpoint=None, opener=None):
        self.key = api_key or os.environ.get('OPENAI_API_KEY')
        if not self.key:
            raise ValueError('Connect an OpenAI key in Model settings, or set OPENAI_API_KEY.')
        self.endpoint = endpoint or os.environ.get('HEALTH_ENDPOINT', 'https://api.openai.com/v1/responses')
        if not self.endpoint.startswith('https://'):
            raise ValueError('The model endpoint must use HTTPS.')
        self.opener = opener

    def __call__(self, *, stage, model, effort, instructions, input, schema, tools, tool_choice=None):
        payload = {'model': model, 'store': False, 'max_output_tokens': 5000,
                   'instructions': instructions, 'input': input,
                   'text': {'format': {'type': 'json_schema', 'name': stage, 'strict': True, 'schema': schema}},
                   'include': ['reasoning.encrypted_content']}
        if effort and effort != 'none':
            payload['reasoning'] = {'effort': effort}
        if tools:
            payload.update(tools=tools, parallel_tool_calls=False)
            if tool_choice:
                payload['tool_choice'] = tool_choice
        return post_json(self.endpoint, payload, self.key, opener=self.opener)


class FirecrawlSearch:
    def __init__(self, api_key=None, domains=None, opener=None):
        self.key = api_key or os.environ.get('FIRECRAWL_API_KEY')
        self.domains = domains or ['nhs.uk', 'cdc.gov', 'who.int', 'medlineplus.gov', 'nice.org.uk']
        self.opener = opener

    def __call__(self, query):
        payload = {'query': query, 'limit': 3, 'sources': ['web'], 'includeDomains': self.domains,
                   'scrapeOptions': {'formats': ['markdown']}}
        body = post_json('https://api.firecrawl.dev/v2/search', payload, self.key, timeout=20, opener=self.opener)
        if not isinstance(body, dict) or not body.get('success'):
            raise RuntimeError('Firecrawl search was unavailable. No current sources were retrieved.')
        data = body.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('web'), list):
            raise RuntimeError('Firecrawl returned an invalid search response.')
        web = data['web']
        self.source_metadata = {row['url']:{'kind':'fetched' if isinstance(row.get('markdown'),str) and row['markdown'].strip() else 'snippet','retrieved_at':time.time()} for row in web[:3] if isinstance(row,dict) and isinstance(row.get('url'),str)}
        return [{'url': row['url'], 'title': row['title'] if isinstance(row.get('title'), str) else row['url'],
                 'content': next((row[k][:8000] for k in ('markdown', 'description') if isinstance(row.get(k), str)), '')}
                for row in web[:3] if isinstance(row, dict) and isinstance(row.get('url'), str)]
