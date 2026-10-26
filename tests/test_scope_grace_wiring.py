"""Regression coverage for the actual health handler and Runtime configuration boundary."""
import copy,json,math,tempfile,time,unittest
from datetime import datetime,timezone
from pathlib import Path
from reminders import Reminders
from runtime import Runtime
from server import handler_for,RuntimeSettings
from delivery import EmailAuthorization
from health_agent import CONFIG_PATH
from tests import test_server as web_fixture

A='a'*32
B='b'*32
class ScopeGraceWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'synthetic.db';self.cfg=json.loads(CONFIG_PATH.read_text());self.cfg['reminders']={'enabled':True,'grace_seconds':17};self.now=time.time()
    def other_due(self):
        store=Reminders(self.path,clock=lambda:self.now-3600)
        self.addCleanup(store.close)
        due=datetime.fromtimestamp(math.floor(self.now/60)*60-120,timezone.utc)
        event=store.create(B,'Other household fictional notice',due.date().isoformat(),due.strftime('%H:%M'),'UTC')
        auth=EmailAuthorization(enabled=True,household=B,sender='sender@example.invalid',recipient='recipient@example.invalid',consent=True)
        store.outbox.route_email(B,event['id'],auth)
        store.clock=lambda:self.now
        store.outbox.clock=store.clock
        return store,event
    def test_household_health_filters_queue_and_email_pending(self):
        store,_=self.other_due();own=store.outbox.health(A);other=store.outbox.health(B);operator=store.outbox.health()
        self.assertIsNone(own['oldest_due_age_seconds']);self.assertFalse(own['email_worker_degraded']);self.assertGreater(other['oldest_due_age_seconds'],0);self.assertTrue(other['email_worker_degraded']);self.assertGreater(operator['oldest_due_age_seconds'],0)
    def test_http_handler_uses_current_session_not_global_or_query_scope(self):
        self.other_due();web=web_fixture.WebTests();web.setUp();self.addCleanup(web.temp.cleanup)
        web.handler=handler_for(self.path,config=self.cfg,settings=RuntimeSettings(self.cfg,load_credentials=False))
        response,a,_=web.request('/api/health',cookie='family_session='+A);self.assertEqual(response.status,200);self.assertIsNone(a['scheduler']['oldest_due_age_seconds']);self.assertFalse(a['scheduler']['email_worker_degraded'])
        _,b,_=web.request('/api/health',cookie='family_session='+B);self.assertGreater(b['scheduler']['oldest_due_age_seconds'],0);self.assertTrue(b['scheduler']['email_worker_degraded'])
        response,_,_=web.request('/api/health?household='+B,cookie='family_session='+A);self.assertEqual(response.status,404)
    def test_runtime_constructor_uses_nondefault_configured_grace(self):
        runtime=Runtime(self.path,None,config=self.cfg,clock=lambda:1792908000);self.addCleanup(runtime.close)
        out=runtime.chat(A,'Remind me at 06:00 on 2026-12-25 in UTC to review a fictional check-in.')
        self.assertEqual(out['route'],'reminder');event=runtime.reminders.list(A)[0];occ=runtime.reminders.outbox.occurrences(A,event['id'])[0]
        self.assertEqual(runtime.reminders.outbox.grace,17);self.assertEqual(occ['expires']-occ['due'],17)
    def test_runtime_config_missing_grace_uses_app_default_300(self):
        self.cfg['reminders'].pop('grace_seconds');runtime=Runtime(self.path,None,config=self.cfg);self.addCleanup(runtime.close);self.assertEqual(runtime.reminders.outbox.grace,300)
    def test_dialogue_worker_status_does_not_inherit_other_email_queue(self):
        store,_=self.other_due();future=datetime.fromtimestamp(self.now+7200,timezone.utc);own=store.create(A,'Own fictional notice',future.date().isoformat(),future.strftime('%H:%M'),'UTC')
        with store.db:store.db.execute('INSERT OR REPLACE INTO scheduler_health VALUES (?,?)',('local-sink',self.now))
        from reminders import command
        result=command(store,A,"What's the current status?",self.now,True,{'last_reminder_id':own['id']})
        self.assertIn('recent_heartbeat',result);self.assertNotIn('Other household',result)
