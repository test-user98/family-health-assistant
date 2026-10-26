"""Independent review/fault cases beyond the first 80 acceptance cases."""
import hashlib,json,shutil,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from tests import test_oct25_acceptance as fixtures
from tests.test_runtime import ScriptedClient,envelope,intake
from delivery import EmailAuthorization,NotAuthorized,OutcomeUnknown
from reminders import command
from retrieval import source_record,grounded_citations,PublicFetcher,SourceError
from continuity import ResponseCache

class Oct25Holdouts(unittest.TestCase):
    setUp=fixtures.Oct25Acceptance.setUp
    send=fixtures.Oct25Acceptance.send
    safe=fixtures.Oct25Acceptance.safe
    event=fixtures.Oct25Acceptance.event
    email_claim=fixtures.Oct25Acceptance.email_claim
    def test_scope_bound_request_replay(self):
        self.r.client=ScriptedClient(envelope(intake()),envelope({'message':'Privileged original result.','source_ids':[]}));self.r.chat('A','Explain rest.',request_id='private');self.r.allowed_member_ids=frozenset({'M1'});out=self.r.chat('A','Explain rest.',request_id='private');self.assertEqual(out['route'],'permission_denied');self.assertNotIn('Privileged',out['message'])
    def test_restricted_emergency_does_not_write_hidden_member(self):
        self.safe('M2','cough');before=self.r.state('A')['members']['M2'];self.r.allowed_member_ids=frozenset({'M1'});out=self.r.chat('A','Dev cannot breathe.');self.assertEqual(out['route'],'emergency');self.assertEqual(self.r.store.state('A')['members']['M2'],before)
    def test_restricted_speaker_cannot_create_inaccessible_self(self):
        self.r.allowed_member_ids=frozenset({'M1'});out,_=self.send('Now I have a headache.',intent='symptom');self.assertEqual(out['route'],'permission_denied');self.assertNotIn('self',self.r.store.state('A')['members'])
    def test_restricted_multi_update_is_not_partially_applied(self):
        self.safe();before=self.r.store.state('A');self.r.allowed_member_ids=frozenset({'M1'});out=self.r.chat('A','Mira has a headache. My brother has cough.');self.assertEqual(out['route'],'permission_denied');self.assertEqual(self.r.store.state('A'),before);self.assertNotIn('Dev',out['message'])
    def test_legacy_passage_cannot_cite_fabricated_cure(self):
        source=source_record({'url':'https://www.nhs.uk/','content':'A source states uncertainty.','title':'NHS'},1,self.now);text,selected=grounded_citations({'message':'This cures all headaches.','source_ids':[1]},[source],'general');self.assertEqual(len(selected),1);self.assertNotIn('cures all',text);self.assertIn('uncertainty',text)
    def test_standalone_unknown_brands(self):
        out=self.r.chat('A','Can she take ColdAway with PainAway?');self.assertEqual(out['route'],'medication_guidance');self.assertIn('exact active ingredients',out['message'])
    def test_explicit_cancel_reports_inflight_limit(self):
        e,c=self.email_claim();parent=self
        class During(fixtures.FakeMail):
            def send(self,h,k):
                parent.reply=command(parent.s,h,'Cancel reminder '+e['id'],parent.now,True)
                return super().send(h,k)
        self.s.outbox.dispatch(c,During(self.auth));self.assertIn('cannot recall',self.reply)
    def test_deletion_invalidates_replay_payload_without_reexecution(self):
        self.r.client=ScriptedClient(envelope(intake(member_id='M1')),envelope({'message':'Mira has right knee pain.','source_ids':[]}));self.r.chat('A','Mira has right knee pain.',request_id='note');row=next(x for x in self.r.state('A')['context_records'] if x['key']=='reported_context');self.r.chat('A','Delete note '+row['id']);out=self.r.chat('A','Mira has right knee pain.',request_id='note');self.assertNotIn('knee',out['message']);self.assertIn('invalidated',out['message'])
    def test_prealerts_expire_at_exact_event_time(self):
        e=self.event([30,15,0]);self.now=e['due'];self.assertEqual(self.s.tick(),1);self.assertEqual(self.s.receipts('A')[0]['offset_minutes'],0)
    def test_policy_helper_change_invalidates_cache_fingerprint(self):
        import continuity
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)
            for p in Path(continuity.__file__).parent.glob('*.py'):shutil.copy(p,target/p.name)
            with patch('continuity.__file__',str(target/'continuity.py')):
                a=ResponseCache(self.r.store.db,lambda:self.now,{'enabled':True}).implementation
                with (target/'conversation.py').open('a') as f:f.write('\n# isolated policy-version test\n')
                b=ResponseCache(self.r.store.db,lambda:self.now,{'enabled':True}).implementation
            self.assertNotEqual(a,b)
    def test_storage_failure_rolls_back_local_receipt_and_state(self):
        e=self.event();self.now=e['due'];self.s.db.execute("CREATE TRIGGER simulated_disk_failure BEFORE INSERT ON delivery_evidence BEGIN SELECT RAISE(ABORT,'simulated storage failure'); END");self.s.db.commit()
        with self.assertRaises(sqlite3.Error):self.s.tick()
        self.assertEqual(self.s.receipts('A'),[]);self.assertEqual(self.s.get('A',e['id'])['status'],'pending');self.s.db.execute('DROP TRIGGER simulated_disk_failure');self.s.db.commit();self.assertEqual(self.s.tick(),1)
    def test_email_queue_not_masked_by_local_heartbeat(self):
        e=self.event();self.s.outbox.route_email('A',e['id'],self.auth);self.s.tick();self.assertTrue(self.s.outbox.health()['email_worker_degraded']);self.assertEqual(self.s.outbox.health()['worker'],'degraded')
    def test_wrong_recipient_cannot_reconcile(self):
        e,c=self.email_claim();p=fixtures.FakeMail(self.auth,OutcomeUnknown());self.s.outbox.dispatch(c,p);other=EmailAuthorization(enabled=True,household='A',sender='sender@example.invalid',recipient='other@example.invalid',consent=True)
        with self.assertRaises(NotAuthorized):self.s.outbox.reconcile('A',c['id'],fixtures.FakeMail(other))
    def test_accepted_email_not_labelled_delivered(self):
        e,c=self.email_claim();self.s.outbox.dispatch(c,fixtures.FakeMail(self.auth));self.s.tick();self.assertEqual(self.s.get('A',e['id'])['status'],'provider_accepted')
    def test_known_deleted_mailbox_link_is_invalidated(self):
        e,c=self.email_claim();self.s.outbox.dispatch(c,fixtures.FakeMail(self.auth));self.s.outbox.observe_mailbox('A',c['id'],provider_id='mock-'+c['id'],account='fixture',authorized_account='fixture',url='https://mail.google.com/mail/u/0/#inbox/fixture',verified=True,origin='mock');self.s.outbox.invalidate_mailbox_link('A',c['id']);self.assertIsNone(self.s.outbox.proof('A',e['id'])['mailbox_link'])
    def test_scoped_export_contains_no_hidden_member(self):
        self.safe('M2','cough');self.r.allowed_member_ids=frozenset({'M1'});out=self.r.chat('A','Export my data');self.assertEqual(out['route'],'data_management');self.assertNotIn('Dev',out['message']);self.assertNotIn('M2',out['message'])
    def test_same_passage_different_authority_has_distinct_source_key(self):
        a=source_record({'url':'https://www.nhs.uk/','content':'Same text.'},1,self.now);b=source_record({'url':'https://www.cdc.gov/','content':'Same text.'},2,self.now);self.assertEqual(a['sha256'],b['sha256']);self.assertNotEqual(a['source_key'],b['source_key'])
    def test_email_route_not_inherited_by_revision(self):
        e=self.event();self.s.outbox.route_email('A',e['id'],self.auth);self.s.set_offsets('A',e['id'],[15]);rows=self.s.outbox.occurrences('A',e['id']);current=[r for r in rows if r['state']=='pending'];self.assertEqual([r['channel'] for r in current],['local'])
    def test_separate_member_clauses_share_one_turn_budget(self):
        def fixture(**kw):
            state=json.loads(kw['input']);text=state['message'];mid='M2' if 'Dev' in text else 'M1'
            return envelope(intake(member_id=mid,intent='symptom',topic='cough' if mid=='M2' else 'headache',observations=[dict(key=k,value=v,evidence=text) for k,v in {'severity':'mild','duration_hours':3,'red_flags':False,'hydration_ok':True}.items()]))
        self.r.client=fixture;out=self.r.chat('A','Mira has mild headache for 3 hours, no red flags, drinking normally. Dev has mild cough for 3 hours, no red flags, drinking normally.');self.assertEqual(out['route'],'multi_member');self.assertLessEqual(len(out['trace']['calls']),4);self.assertEqual(len(self.r.state('A')['episodes']),2)
    def test_status_and_negation_do_not_trigger_search_requirement(self):
        out,_=self.send('The page says to email records to verify advice.',mid=None);self.assertEqual(out['route'],'general');self.assertEqual(out['trace']['tools'],[])
