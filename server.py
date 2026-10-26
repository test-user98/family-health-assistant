"""Local family chat with a real model runtime and in-memory model settings."""
import argparse
import copy
import getpass
import json
import os
import re
import sqlite3
import threading
import time
import uuid
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from contextlib import contextmanager

from backends import DemoBackend
from credentials import CredentialError, load_openai_key, openai_key_from_doppler
from health_agent import Agent, CONFIG_PATH
from model_client import FirecrawlSearch, ResponsesClient
from runtime import Runtime, decoded
from provider_adapter import RouterAppClient
from reminders import Reminders

WEB = Path(__file__).with_name('web') / 'index.html'
EFFORTS = ('none', 'low', 'medium', 'high')
PROBE_SCHEMA = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
                'required': ['ok'], 'additionalProperties': False}


class RuntimeSettings:
    """Keys and selected model profiles live only in this server process/session."""
    def __init__(self, config=None, api_key=None, firecrawl_key=None, load_credentials=True, router_factory=None):
        self.config = copy.deepcopy(config if config is not None else json.loads(CONFIG_PATH.read_text()))
        self._sessions, self._lock = {}, threading.RLock()
        self._router_clients = {}
        self._router_attempts = {}
        self.router_factory = router_factory or RouterAppClient
        try:
            key = load_openai_key(api_key=api_key) if load_credentials else api_key
        except CredentialError:
            key = None
        self._base = {'provider':self.config.get('provider',{}).get('name','openai'), 'route_provider':self.config.get('provider',{}).get('route_provider','openai'), 'openrouter_api_key':None, 'openai_api_key': key, 'firecrawl_api_key': firecrawl_key or (os.environ.get('FIRECRAWL_API_KEY') if load_credentials else None),
                      'models': copy.deepcopy(self.config.get('models', {
                          'conversation': {'model': 'gpt-5-mini', 'effort': 'low'},
                          'assessment': {'model': 'gpt-5-mini', 'effort': 'medium'}}))}
        if os.environ.get('HEALTH_MODEL'):
            for profile in self._base['models'].values():
                profile['model'] = os.environ['HEALTH_MODEL']

    def _snapshot(self, session):
        with self._lock:
            return copy.deepcopy(self._sessions.get(session, self._base))

    def metadata(self, session):
        current = self._snapshot(session)
        return {'configured': bool(current['openrouter_api_key'] if current['provider']=='openrouter' else current['openai_api_key']), 'provider':current['provider'], 'route_provider':current['route_provider'], 'session_budget_usd':str(self.config.get('provider',{}).get('session_budget_usd','.90')),
                'models': current['models'],
                'web_search': {'provider': 'firecrawl', 'configured': True,
                               'authenticated': bool(current['firecrawl_api_key'])}}

    def engine(self, session, db_path):
        current = self._snapshot(session)
        config = copy.deepcopy(self.config)
        config['models'] = current['models']
        client = self._client(session,current)
        search = FirecrawlSearch(api_key=current['firecrawl_api_key'])
        return Runtime(db_path, client, search=search, config=config)

    def _router_candidate(self,session,current):
        # Re-testing an unchanged connection cannot reset stopped/uncertain spend.
        identity=(session,current['openrouter_api_key'],current['route_provider'])
        with self._lock:
            if identity not in self._router_attempts:
                self._router_attempts[identity]=self.router_factory(identity[1],identity[2],str(self.config.get('provider',{}).get('session_budget_usd','.90')))
            return self._router_attempts[identity]

    def _client(self,session,current):
        if current['provider']=='openai':
            return ResponsesClient(api_key=current['openai_api_key']) if current['openai_api_key'] else None
        if not current['openrouter_api_key']:return None
        with self._lock:
            identity=(current['openrouter_api_key'],current['route_provider'])
            saved=self._router_clients.get(session)
            if saved is None or saved[0]!=identity:
                client=self._router_candidate(session,current)
                self._router_clients[session]=(identity,client)
            return self._router_clients[session][1]

    def configure(self, session, payload):
        fields = {'conversation_model', 'conversation_effort', 'assessment_model', 'assessment_effort'}
        secrets = {'openai_api_key', 'openrouter_api_key', 'firecrawl_api_key', 'doppler_token'}
        options = {'provider','route_provider'}
        if not isinstance(payload, dict) or not fields <= set(payload) or not set(payload) <= fields | secrets | options:
            raise ValueError('Supply both model IDs and their reasoning efforts.')
        current = self._snapshot(session)
        provider=payload.get('provider', 'openai' if payload.get('openai_api_key') or payload.get('doppler_token') else current['provider'])
        tag=payload.get('route_provider',current['route_provider'])
        if provider not in ('openai','openrouter') or not isinstance(tag,str) or not re.fullmatch(r'[a-z0-9._-]{1,80}',tag):raise ValueError('Select a supported provider and explicit route.')
        current.update(provider=provider,route_provider=tag)
        for stage in ('conversation', 'assessment'):
            model, effort = payload[stage + '_model'], payload[stage + '_effort']
            if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', model):
                raise ValueError('Use a valid model ID without spaces.')
            if not isinstance(effort, str) or effort not in EFFORTS:
                raise ValueError('Reasoning effort must be none, low, medium, or high.')
            current['models'][stage] = {'model': model, 'effort': effort}
        for field in secrets:
            value = payload.get(field)
            if value is not None and (not isinstance(value, str) or len(value) > 4096):
                raise ValueError('Use a valid provider credential.')
        direct, token = (payload.get('openai_api_key') or '').strip(), (payload.get('doppler_token') or '').strip()
        if direct and token:
            raise ValueError('Use either an OpenAI key or a Doppler service token.')
        if token:
            try:
                current['openai_api_key'] = openai_key_from_doppler(token)
            except CredentialError as error:
                raise ValueError(str(error)) from None
            except Exception:
                raise ValueError('Could not retrieve OPENAI_API_KEY from Doppler. Check the scoped service token.') from None
        elif direct:
            current['openai_api_key'] = direct
        firecrawl = (payload.get('firecrawl_api_key') or '').strip()
        if firecrawl:
            current['firecrawl_api_key'] = firecrawl
        router_key=(payload.get('openrouter_api_key') or '').strip()
        if router_key:current['openrouter_api_key']=router_key
        if provider=='openrouter':
            if token or direct:raise ValueError('Use the separate OpenRouter key field for this provider.')
            if not current['openrouter_api_key']:raise ValueError('Enter an OpenRouter key for this session.')
            if any(p['effort']!='none' for p in current['models'].values()):raise ValueError('Use provider-default effort for the OpenRouter app adapter.')
            # Test a candidate without replacing an existing working connection.
            client=self._router_candidate(session,current)
        else:
            if not current['openai_api_key']:raise ValueError('Enter an OpenAI key or a scoped Doppler service token.')
            client=ResponsesClient(api_key=current['openai_api_key'])
        for stage, profile in current['models'].items():
            try:
                envelope = client(stage='connection_test', model=profile['model'], effort=profile['effort'],
                                  instructions='Return JSON {"ok":true}. This is a connection test; do not use tools.',
                                  input='Test this model and reasoning effort. No patient data is included.',
                                  schema=PROBE_SCHEMA, tools=[])
                answer, calls = decoded(envelope)
                if calls or not isinstance(answer, dict) or set(answer) != {'ok'} or answer['ok'] is not True:
                    raise ValueError('Invalid connection response.')
            except Exception:
                raise ValueError(f'The {stage} model connection test failed. Check the key, model access, and supported reasoning effort.') from None
        if firecrawl:
            try:
                FirecrawlSearch(api_key=firecrawl)('NHS general health guidance')
            except Exception:
                raise ValueError('The Firecrawl connection test failed. Check the search key.') from None
        with self._lock:
            self._sessions[session] = current
            old=self._router_clients.pop(session,None)
            # Prior connection attempts retain budget state until process exit.
            if provider=='openrouter':self._router_clients[session]=((current['openrouter_api_key'],tag),client)
        return self.metadata(session)


@contextmanager
def database(path):
    db = sqlite3.connect(str(path))
    try:
        with db:
            yield db
    finally:
        db.close()


def handler_for(db_path, backend=None, config=None, backend_name='llm', settings=None, access_for=None, review_adapter=None):
    config = copy.deepcopy(config if config is not None else json.loads(CONFIG_PATH.read_text()))
    legacy = backend is not None
    name = 'demo' if isinstance(backend, DemoBackend) else backend_name
    settings = settings or RuntimeSettings(config, load_credentials=not legacy)
    with database(db_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS chat_messages (id INTEGER PRIMARY KEY, session TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, at REAL NOT NULL, metadata TEXT NOT NULL DEFAULT "{}")')
        if 'metadata' not in {row[1] for row in db.execute('PRAGMA table_info(chat_messages)')}:
            db.execute('ALTER TABLE chat_messages ADD COLUMN metadata TEXT NOT NULL DEFAULT "{}"')
    session_locks, locks_guard = {}, threading.Lock()

    def chat_lock(session):
        # Keep turns ordered within a household without blocking other households.
        with locks_guard:
            return session_locks.setdefault(session, threading.Lock())

    def engine(session):
        return Agent(db_path, backend, config) if legacy else settings.engine(session, db_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Never log health conversations, credential payloads, or session cookies.

        def session(self):
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get('Cookie', ''))
            except Exception:
                pass
            current = cookie.get('family_session')
            return current.value if current and re.fullmatch(r'[a-f0-9]{32}', current.value) else uuid.uuid4().hex

        def respond(self, status, data, session, content_type='application/json; charset=utf-8'):
            payload = json.dumps(data, ensure_ascii=False).encode() if content_type.startswith('application/json') else data
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.send_header('Set-Cookie', f'family_session={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000')
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            session = self.session()
            if self.path in ('/', '/index.html'):
                return self.respond(200, WEB.read_bytes(), session, 'text/html; charset=utf-8')
            if self.path == '/favicon.ico':
                return self.respond(204, b'', session, 'image/x-icon')
            if self.path == '/api/runtime':
                return self.respond(200, {'runtime': settings.metadata(session), 'backend': name}, session)
            if self.path == '/api/health':
                if not config.get('reminders',{}).get('enabled'):
                    health={'app':'alive','scheduler':{'worker':'disabled','guaranteed_delivery':False}}
                    if config.get('human_review',{}).get('gmail_worker'):
                        from gmail_worker import GmailReviewWorker
                        mail_worker=GmailReviewWorker(db_path,config['human_review']['credential_directory'],config=config)
                        try:health['gmail_review']=mail_worker.health()
                        finally:mail_worker.close()
                    return self.respond(200,health,session)
                sink=Reminders(db_path,grace_seconds=config.get('reminders',{}).get('grace_seconds',300))
                try:
                    health={'app':'alive','scheduler':sink.outbox.health(session)}
                    if config.get('human_review',{}).get('gmail_worker'):
                        from gmail_worker import GmailReviewWorker
                        mail_worker=GmailReviewWorker(db_path,config['human_review']['credential_directory'],config=config)
                        try:health['gmail_review']=mail_worker.health()
                        finally:mail_worker.close()
                    return self.respond(200,health,session)
                finally:
                    sink.close()
            if self.path not in ('/api/state', '/api/state?history=0'):
                return self.respond(404, {'error': 'Not found'}, session)
            include_history = self.path == '/api/state'
            agent = Agent(db_path, backend if legacy else lambda _: None, config)
            try:
                state = agent.state(session)
                if access_for is not None:
                    from conversation import view
                    state=view(state,access_for(session))
                rows = []
                if include_history and access_for is None:
                    with database(db_path) as db:
                        rows = db.execute('SELECT id,role, content, at, metadata FROM chat_messages WHERE session=? ORDER BY id', (session,)).fetchall()
                members = [dict(id=mid, name=m['name'], relationship=m.get('relationship', 'family member'),
                                age_months=m['age_months'], pronouns=m.get('pronouns', [])) for mid, m in state['members'].items()]
                messages = []
                for message_id,role, content, at, metadata in rows:
                    saved = json.loads(metadata)
                    messages.append(dict(message_id=message_id,role=role, content=content, at=at,
                                         **{k: saved[k] for k in ('trace', 'sources') if k in saved}))
                data = {'members': members, 'runtime': settings.metadata(session),
                        'backend': name, 'session_id': session}
                data['learning_enabled']=bool(config.get('learning',{}).get('enabled'))
                if config.get('human_review',{}).get('enabled'):
                    from human_review import HumanReview
                    options=config['human_review']
                    cases=HumanReview(agent.db,reviewer=options.get('reviewer','sipanijai@gmail.com'),inbox=options.get('inbox','review@local.invalid')).view(session,None if access_for is None else access_for(session))
                    data['human_reviews']=cases
                    if options.get('gmail_worker'):
                        from gmail_worker import GmailReviewWorker
                        mail_worker=GmailReviewWorker(db_path,options['credential_directory'],config=config)
                        try:
                            data['pending_review_replies']=mail_worker.pending(session,None if access_for is None else access_for(session))
                            data['gmail_review_health']=mail_worker.health()
                        finally:mail_worker.close()
                    if include_history:
                        for case in cases:
                            status_text={'queued':'Queued locally; no email delivery or answer is confirmed.','notified':'Notification transport accepted it; inbox receipt and a reviewer answer remain unconfirmed.','answered':'An authenticated test human response was accepted.','notification_pending':'Notification submission is in progress; outcome is not yet confirmed.','notification_unknown':'Notification outcome unknown; do not assume delivery.','revoked':'Reviewer access revoked.'}.get(case['status'],'Review status unavailable.')
                            messages.append(dict(role='assistant',content='Optional test human review '+case['id']+': '+status_text,at=case['created_at']))
                            for reply in case['replies']:
                                messages.append(dict(role='human_reviewer',content=reply['text'],at=reply['received_at'],review=dict(case_id=case['id'],label=case['label'],reviewer=case['reviewer'],authored_at=reply['authored_at'],received_at=reply['received_at'])))
                        messages.sort(key=lambda m:m['at'])
                if include_history:
                    data['messages'] = messages
                self.respond(200, data, session)
            except Exception:
                self.respond(500, {'error': 'Could not load this conversation. Please try again.'}, session)
            finally:
                agent.close()

        def do_POST(self):
            session = self.session()
            if self.path not in ('/api/chat', '/api/settings','/api/review/reply','/api/review/confirm','/api/feedback'):
                return self.respond(404, {'error': 'Not found'}, session)
            if self.path=='/api/review/reply' and (review_adapter is None or not config.get('human_review',{}).get('enabled')):
                return self.respond(404,{'error':'No authenticated review ingress is configured.'},session)
            if self.path=='/api/feedback' and not config.get('learning',{}).get('enabled'):
                return self.respond(404,{'error':'Local feedback is disabled.'},session)
            origin = self.headers.get('Origin')
            if self.path=='/api/review/confirm' and (not config.get('human_review',{}).get('gmail_worker') or origin not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}')):
                return self.respond(403,{'error':'Open the configured local review UI to confirm authorship.'},session)
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
                return self.respond(403, {'error': 'Open this app from its local URL.'}, session)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 30000 or self.headers.get_content_type() != 'application/json':
                    raise ValueError()
                body=self.rfile.read(size)
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise ValueError()
            except (ValueError, TypeError):
                return self.respond(400, {'error': 'Please send a valid JSON request.'}, session)
            if self.path=='/api/feedback':
                from learning import Learning,LearningError
                if set(data)!={'message_id','rating','category','consent'} or type(data['message_id']) is not int:
                    return self.respond(400,{'error':'Invalid feedback fields.'},session)
                if access_for is not None:return self.respond(403,{'error':'Feedback for restricted message views is unavailable.'},session)
                workflow=Learning(db_path,config)
                try:
                    fid=workflow.feedback(session,data['message_id'],data['rating'],data['category'],data['consent'])
                    return self.respond(200,{'feedback_id':fid,'status':'saved_locally_not_training'},session)
                except LearningError:
                    return self.respond(403,{'error':'Feedback was not accepted.'},session)
                finally:workflow.close()
            if self.path=='/api/review/reply':
                from human_review import HumanReview,ReviewError
                try:
                    with database(db_path) as db:
                        options=config['human_review']
                        status=HumanReview(db,reviewer=options.get('reviewer','sipanijai@gmail.com'),inbox=options.get('inbox','review@local.invalid')).accept(body,self.headers.get('X-Review-Signature',''),review_adapter)
                    return self.respond(200,{'status':status},session)
                except ReviewError:
                    return self.respond(403,{'error':'Reply was not accepted.'},session)
            if self.path=='/api/review/confirm':
                from gmail_worker import GmailReviewWorker
                from human_review import ReviewError
                if set(data)!={'event_id','confirm_authorship'} or not isinstance(data['event_id'],str) or len(data['event_id'])>256 or data['confirm_authorship'] is not True:
                    return self.respond(400,{'error':'Explicit reply authorship confirmation required.'},session)
                options=config['human_review']
                mail_worker=GmailReviewWorker(db_path,options['credential_directory'],config=config)
                try:
                    status=mail_worker.confirm(session,data['event_id'],attest=True,allowed=None if access_for is None else access_for(session))
                    return self.respond(200,{'status':status},session)
                except ReviewError:
                    return self.respond(403,{'error':'Reply was not accepted for this household.'},session)
                finally:mail_worker.close()
            if self.path == '/api/settings':
                if legacy:
                    return self.respond(400, {'error': 'Restart without --backend demo to connect real models.'}, session)
                try:
                    metadata = settings.configure(session, data)
                except ValueError as error:
                    return self.respond(400, {'error': str(error)}, session)
                except Exception:
                    return self.respond(400, {'error': 'Could not test the connection. Check credentials and model access.'}, session)
                return self.respond(200, {'runtime': metadata, 'backend': name}, session)
            if set(data) not in ({'message'},{'message','request_id'}) or not isinstance(data['message'], str) or not data['message'].strip():
                return self.respond(400, {'error': 'Please send a nonempty message.'}, session)
            with chat_lock(session):
                agent = None
                try:
                    agent = engine(session)
                    if access_for is not None and isinstance(agent, Runtime):
                        agent.allowed_member_ids=frozenset(access_for(session))
                    result = agent.chat(session, data['message'], request_id=data['request_id']) if 'request_id' in data and isinstance(agent, Runtime) else agent.chat(session,data['message'])
                    metadata = {key: result[key] for key in ('trace', 'sources') if key in result}
                    with database(db_path) as db:
                        if result.get('trace',{}).get('request_replayed'):return self.respond(200,result,session)
                        db.executemany('INSERT INTO chat_messages(session,role,content,at,metadata) VALUES (?,?,?,?,?)',
                                       [(session, 'user', data['message'], time.time(), '{}'),
                                        (session, 'assistant', result['message'], time.time(), json.dumps(metadata))])
                except Exception:
                    return self.respond(500, {'error': 'Could not complete this turn. Please try again.'}, session)
                finally:
                    if agent is not None:
                        agent.close()
            self.respond(200, result, session)

    return Handler


def main():
    parser = argparse.ArgumentParser(description='Local family health chat')
    parser.add_argument('--backend', choices=['llm', 'demo'], default='llm')
    parser.add_argument('--db', default='family_health.db')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--reminder-worker', action='store_true', help='Enable local reminders; delivery only to the SQLite notification_sink table while this process is awake')
    parser.add_argument('--gmail-review-worker', action='store_true', help='Opt in to neutral synthetic self-mail review notifications and known-thread polling; human replies require local authorship confirmation')
    parser.add_argument('--gmail-credentials', type=Path, default=Path(__file__).with_name('.local_credentials'), help='Private existing Gmail OAuth directory; never included in Git')
    parser.add_argument('--open', action='store_true', help='Open the chat in your default browser')
    parser.add_argument('--doppler', action='store_true', help='Securely prompt for a scoped Doppler token; never pass it in shell arguments')
    parser.add_argument('--config', type=Path, default=CONFIG_PATH)
    args = parser.parse_args()
    if args.doppler and args.backend == 'demo':
        parser.error('--doppler requires the real model runtime; omit --backend demo.')
    config = json.loads(args.config.read_text())
    if args.reminder_worker:
        config.setdefault('reminders', {})['enabled'] = True
    if args.gmail_review_worker:
        if args.backend=='demo':parser.error('Gmail worker requires the real runtime; use injected fake transports in offline tests.')
        options=config.setdefault('human_review',{})
        if options.get('synthetic_only') is not True or options.get('reviewer')!='sipanijai@gmail.com' or options.get('inbox')!='sipanijai@gmail.com':
            parser.error('Gmail worker needs explicit synthetic_only:true and the approved personal reviewer/inbox in the chosen config.')
        options.update(enabled=True,gmail_worker=True,credential_directory=str(args.gmail_credentials.resolve()))
    key = None
    if args.doppler:
        try:
            key = openai_key_from_doppler(getpass.getpass('Doppler service token (hidden): '))
        except CredentialError as error:
            parser.exit(2, str(error) + '\n')
    settings = RuntimeSettings(config, api_key=key, load_credentials=args.backend != 'demo')
    backend = DemoBackend() if args.backend == 'demo' else None
    try:
        httpd = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(args.db, backend, config, args.backend, settings))
    except OSError as error:
        parser.exit(1, f'Could not open the local server port: {error}. Try another port with --port 8001, or run from your terminal if this environment restricts local servers.\n')
    print(f'Family health chat: http://127.0.0.1:{httpd.server_port} ({args.backend})', flush=True)
    if args.backend == 'llm' and not settings.metadata('startup')['configured']:
        print('Open Model settings in the chat to connect your API key and test both models.', flush=True)
    if args.open:
        webbrowser.open(f'http://127.0.0.1:{httpd.server_port}')
    stop_reminders = threading.Event()
    def reminder_worker():
        sink = Reminders(args.db,grace_seconds=config.get('reminders',{}).get('grace_seconds',300))
        try:
            while not stop_reminders.is_set():
                try:
                    sink.tick()
                except sqlite3.Error:
                    pass  # Durable pending records are retried next poll, never marked sent on error.
                stop_reminders.wait(5)
        finally:
            sink.close()
    worker = threading.Thread(target=reminder_worker, daemon=True) if args.reminder_worker else None
    if worker:
        worker.start()
    def gmail_worker():
        from gmail_worker import GmailReviewWorker
        mail_worker=GmailReviewWorker(args.db,args.gmail_credentials,config=config)
        try:
            while not stop_reminders.is_set():
                mail_worker.tick()
                stop_reminders.wait(60)
        finally:mail_worker.close()
    mail_thread=threading.Thread(target=gmail_worker,daemon=True) if args.gmail_review_worker else None
    if mail_thread:mail_thread.start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_reminders.set()
        if worker:
            worker.join(timeout=6)
        if mail_thread:mail_thread.join(timeout=2)
        httpd.server_close()


if __name__ == '__main__':
    main()
