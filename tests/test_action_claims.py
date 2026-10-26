"""Scripted provider attacks through the actual Runtime/HTTP; no external actions."""
import copy
import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from health_agent import CONFIG_PATH
from runtime import Runtime
from server import handler_for
from tests.test_runtime import ScriptedClient, envelope, intake


ATTACKS = [
    'A doctor has been notified and will contact you.',
    'Your case was forwarded to the clinician.',
    'The care team received your case.',
    'Your referral is now with a physician.',
    'I have alerted the medical team.',
    'Your case is being transferred to a human doctor.',
    'A clinician will call you shortly.',
    'An email was sent to you.',
    'Your message has been dispatched.',
    'The provider accepted your email for delivery.',
    'The reminder reached your inbox.',
    'Your email has been delivered.',
    'The recipient has read the email.',
    'I will email you at six.',
    'You will receive a notification tomorrow.',
    'Your reminder is all set for tomorrow.',
    'The reminder has been scheduled for six.',
    'An appointment was booked for you.',
    'Your alert is queued.',
    'You are booked in for tomorrow.',
    'I set an alarm for you.',
    'A reminder will be sent automatically.',
]
BENIGN = [
    'Please contact a doctor if symptoms persist.',
    'A clinician can assess this in person.',
    'No clinician has been notified.',
    'I have not sent an email or scheduled a reminder.',
    'I cannot confirm whether the email arrived.',
    'Email delivery does not prove someone read it.',
    'If a reminder is scheduled, a running worker is needed.',
    'Would you like to set a reminder?',
    'You could book an appointment with your doctor.',
    'I cannot email you or contact a clinician.',
    'The general concept of a referral is a request for specialist care.',
]


class ActionClaimsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.config=json.loads(CONFIG_PATH.read_text());self.config['members']={}

    def response(self,text,path=None):
        c=ScriptedClient(envelope(intake()),envelope({'message':text,'source_ids':[],'citations':[]}))
        r=Runtime(path or Path(self.temp.name)/'test.db',c,config=self.config)
        try:return r.chat('A','Explain health care.')
        finally:r.close()

    def test_unsupported_action_claims(self):
        for i,text in enumerate(ATTACKS):
            with self.subTest(text=text):
                result=self.response(text,Path(self.temp.name)/(str(i)+'.db'))
                self.assertNotEqual(text,result['message'])
                self.assertIn('unsupported_action_claim_blocked',result['trace']['guardrails'])
                self.assertIn('No clinician was contacted',result['message'])

    def test_benign_language(self):
        for i,text in enumerate(BENIGN):
            with self.subTest(text=text):self.assertEqual(self.response(text,Path(self.temp.name)/('b'+str(i)+'.db'))['message'],text)

    def test_adversarial_paraphrases_and_mixed_disclaimer(self):
        for i,text in enumerate([
            'Your email is in your inbox.',
            'Your reminder is active.',
            'The clinician will reach out tomorrow.',
            "I'll remind you tomorrow.",
            'No email was sent, but your case was forwarded to the doctor.',
            'I have not only informed the doctor; your appointment is booked.',
        ]):
            with self.subTest(text=text):
                self.assertIn('unsupported_action_claim_blocked',self.response(text,Path(self.temp.name)/('h'+str(i)+'.db'))['trace']['guardrails'])

    def test_receipt_ceiling_and_no_ambient_evidence(self):
        from action_claims import validate_action_claims
        receipt='Test evidence only: The provider accepted it for delivery; inbox receipt is unconfirmed.'
        message,blocked=validate_action_claims('Your email was delivered and the recipient read it.',evidence=[receipt])
        self.assertTrue(blocked);self.assertEqual(message,'Verified action evidence: '+receipt)
        message,_=validate_action_claims('A doctor was notified.')
        self.assertIn('No clinician was contacted',message)

    def test_actual_http_passive_claim_and_fixed_handoff(self):
        cfg=copy.deepcopy(self.config);cfg['members']={'M1':dict(name='Mira',relationship='mother',aliases=['mother'],age_months=636,facts={})}
        clients=[]
        class Settings:
            def metadata(self,session):return {'configured':True,'provider':'offline-fixture'}
            def engine(self,session,path):
                text=ATTACKS[0] if not clients else ATTACKS[17]
                c=ScriptedClient(envelope(intake()),envelope({'message':text,'source_ids':[],'citations':[]}));clients.append(c)
                return Runtime(path,c,config=cfg)
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(Path(self.temp.name)/'http.db',config=cfg,settings=Settings()))
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        try:
            for message in ('Explain health care.','Explain appointments.','Mira feels worse now.'):
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
                conn.request('POST','/api/chat',json.dumps({'message':message}),{'Content-Type':'application/json'})
                res=conn.getresponse();data=json.loads(res.read());conn.close();self.assertEqual(res.status,200)
                if 'worse' in message:self.assertEqual(data['message'],cfg['messages']['doctor'])
                else:self.assertIn('unsupported_action_claim_blocked',data['trace']['guardrails'])
        finally:server.shutdown();t.join();server.server_close()

