"""Actual temporary SQLite/HTTP; mail provider is a declared in-memory fake."""
import base64
import copy
import http.client
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path

from gmail_worker import GmailReviewWorker
from human_review import ReviewError
from runtime import Runtime
from server import handler_for
from tests.test_runtime import ScriptedClient, envelope, intake


class FakeMail:
    sends = []
    lock = threading.Lock()
    fail_after_accept = False
    fail_observe = False
    thread = None

    def __init__(self, *args, **kwargs):
        self.receipt_sink = None

    def authenticate(self):
        return {'profile_verified': True}

    def __call__(self, notification):
        with self.lock:
            self.sends.append(copy.deepcopy(notification))
        self.receipt = dict(message_id='original', thread_id='thread', subject='Synthetic review')
        self.receipt_sink(self.receipt)
        if self.fail_after_accept:
            raise RuntimeError('Synthetic crash after durable receipt')
        return dict(status='accepted', recipient=notification['recipient'])

    def observe_sent_message(self):
        if self.fail_observe:
            raise RuntimeError('Synthetic lookup unavailable')
        return dict(self.receipt, provider_rfc_message_id='<original@example.test>')

    def read_known_thread(self):
        return copy.deepcopy(self.thread)

    def clear(self):
        pass


class GmailWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'synthetic.db'
        self.now = time.time()
        self.cfg = json.loads(Path('config.json').read_text())
        self.cfg['human_review'] = dict(enabled=True, synthetic_only=True, reviewer='sipanijai@gmail.com', inbox='sipanijai@gmail.com', gmail_worker=True, credential_directory=self.tmp.name)
        self.cfg['members'] = {'M1': dict(name='Mira', relationship='mother', age_months=636, aliases=['mother'], facts={})}
        client = ScriptedClient(envelope(intake(member_id='M1', intent='symptom', topic='cough', observations=[dict(key='severity', value='moderate', evidence='moderate')])))
        self.runtime = Runtime(self.path, client, config=self.cfg, clock=lambda: self.now)
        self.addCleanup(self.runtime.close)
        result = self.runtime.chat('a'*32, 'Mira has a moderate cough.')
        self.case = result['human_review']['id']
        FakeMail.sends = []
        FakeMail.fail_after_accept = FakeMail.fail_observe = False
        headers = {'From': 'sipanijai@gmail.com', 'To': 'sipanijai@gmail.com', 'Subject': 'Re: Synthetic review', 'In-Reply-To': '<original@example.test>'}
        FakeMail.thread = dict(id='thread', messages=[dict(id='original'), dict(id='reply', threadId='thread', labelIds=['SENT', 'INBOX'], internalDate=str(int((self.now+1)*1000)), payload=dict(mimeType='text/plain', headers=[dict(name=k, value=v) for k,v in headers.items()], body=dict(data=base64.urlsafe_b64encode(b'Received - synthetic reply').decode())))])
        self.worker = self.make_worker()
        self.addCleanup(self.worker.close)

    def make_worker(self):
        return GmailReviewWorker(self.path, self.tmp.name, config=self.cfg, factory=FakeMail, clock=lambda: self.now+2)

    def test_dispatch_poll_quarantine_then_confirm(self):
        self.worker.tick()
        self.assertEqual(len(FakeMail.sends), 1)
        self.assertNotIn('cough', json.dumps(FakeMail.sends))
        self.assertNotIn('Mira', json.dumps(FakeMail.sends))
        self.assertEqual(self.worker.service.view('a'*32)[0]['status'], 'notified')
        self.assertEqual(self.worker.service.view('a'*32)[0]['replies'], [])
        pending = self.worker.pending('a'*32)
        self.assertEqual(len(pending), 1)
        with self.assertRaises(ReviewError):
            self.worker.confirm('a'*32, pending[0]['event_id'])
        self.assertEqual(self.worker.confirm('a'*32, pending[0]['event_id'], attest=True), 'accepted')
        self.assertEqual(self.worker.service.view('a'*32)[0]['replies'][0]['text'], 'Received - synthetic reply')
        self.assertEqual(self.worker.pending('a'*32), [])

    def test_restart_duplicate_send_and_reply_suppressed(self):
        self.worker.tick()
        other = self.make_worker()
        try:
            other.tick(); other.tick()
            self.assertEqual(len(FakeMail.sends), 1)
            self.assertEqual(len(other.pending('a'*32)), 1)
            self.assertEqual(other.confirm('a'*32, 'gmail:reply', attest=True), 'accepted')
            self.assertEqual(other.confirm('a'*32, 'gmail:reply', attest=True), 'duplicate')
            other.tick()
            self.assertEqual(len(other.service.view('a'*32)[0]['replies']), 1)
        finally:
            other.close()

    def test_durable_acceptance_before_ack_crash_reconciles_without_resend(self):
        FakeMail.fail_after_accept = True
        self.worker.tick()
        self.assertEqual(len(FakeMail.sends), 1)
        self.assertEqual(len(self.worker.pending('a'*32)), 1)
        self.worker.tick()
        self.assertEqual(len(FakeMail.sends), 1)

    def test_metadata_failure_keeps_receipt_and_does_not_resend(self):
        FakeMail.fail_observe = True
        self.worker.tick(); self.worker.tick()
        self.assertEqual(len(FakeMail.sends), 1)
        self.assertEqual(self.worker.health()['outcome'], 'degraded_check_local_configuration_or_known_thread')
        self.assertEqual(self.worker.pending('a'*32), [])
        FakeMail.fail_observe = False
        self.worker.tick()
        self.assertEqual(len(self.worker.pending('a'*32)), 1)

    def test_household_and_member_scope(self):
        self.worker.tick()
        self.assertEqual(self.worker.pending('b'*32), [])
        self.assertEqual(self.worker.pending('a'*32, set()), [])
        for household, allowed in [('b'*32, None), ('a'*32, set())]:
            with self.assertRaises(ReviewError):
                self.worker.confirm(household, 'gmail:reply', attest=True, allowed=allowed)

    def test_forged_thread_or_missing_sent_is_quarantined_without_badge(self):
        FakeMail.thread['messages'][1]['labelIds'] = ['INBOX']
        self.worker.tick()
        self.assertEqual(self.worker.pending('a'*32), [])
        self.assertEqual(self.worker.service.view('a'*32)[0]['status'], 'notified')

    def test_revocation_and_emergency_block_confirmation(self):
        self.worker.tick()
        self.runtime.chat('a'*32, 'Mira cannot breathe.')
        with self.assertRaises(ReviewError):
            self.worker.confirm('a'*32, 'gmail:reply', attest=True)
        self.worker.service.revoke('a'*32, self.case)
        self.assertEqual(self.worker.pending('a'*32), [])

    def test_new_emergency_blocks_unsent_notification(self):
        result=self.runtime.chat('a'*32,'Mira cannot breathe.')
        self.assertEqual(result['route'],'emergency')
        self.worker.tick()
        self.assertEqual(FakeMail.sends,[])

    def test_two_worker_claims_one_send(self):
        def tick(_):
            worker = self.make_worker()
            try:worker.tick()
            finally:worker.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(tick, range(2)))
        self.assertEqual(len(FakeMail.sends), 1)

    def test_disabled_or_other_mailbox_refused(self):
        for change in [dict(synthetic_only=False), dict(reviewer='other@example.test'), dict(enabled=False)]:
            cfg=copy.deepcopy(self.cfg); cfg['human_review'].update(change)
            with self.assertRaises(ReviewError):GmailReviewWorker(self.path, self.tmp.name, config=cfg, factory=FakeMail)

    def test_http_owner_confirmation_origin_scope_and_exact_readback(self):
        self.worker.tick()
        cfg = self.cfg
        class Settings:
            def metadata(self, session):return {'configured':False}
            def engine(self, session, path):return Runtime(path, None, config=cfg)
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.path,config=cfg,settings=Settings()))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(method,path,household='a'*32,origin=True):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            headers={'Cookie':'family_session='+household,'Content-Type':'application/json'}
            if origin:headers['Origin']='http://127.0.0.1:'+str(server.server_port)
            data=json.dumps(dict(event_id='gmail:reply',confirm_authorship=True)) if method=='POST' else None
            c.request(method,path,data,headers);response=c.getresponse();body=json.loads(response.read());status=response.status;c.close();return status,body
        try:
            state=request('GET','/api/state')[1]
            self.assertEqual(len(state['pending_review_replies']),1)
            self.assertFalse(any(m['role']=='human_reviewer' for m in state['messages']))
            self.assertNotIn('challenge',json.dumps(state))
            self.assertEqual(request('POST','/api/review/confirm',origin=False)[0],403)
            self.assertEqual(request('POST','/api/review/confirm',household='b'*32)[0],403)
            self.assertEqual(request('POST','/api/review/confirm')[0],200)
            reply=next(m for m in request('GET','/api/state')[1]['messages'] if m['role']=='human_reviewer')
            self.assertEqual(reply['content'],'Received - synthetic reply')
            self.assertIn('not a verified doctor',reply['review']['label'])
        finally:
            server.shutdown();server.server_close();thread.join(timeout=3)
