"""Additional unseen review scenarios beyond the original reproductions."""
import copy,json,tempfile,unittest
from pathlib import Path
from reminders import Reminders,utc_due,command,ReminderError
from continuity import record_context
from tests import test_continuity_features as features
from tests.test_runtime import ScriptedClient,envelope,intake

# Inherit helpers without rediscovering the feature class in this module.
class HoldoutContinuity(unittest.TestCase):
    setUp = features.ContinuityFeatures.setUp
    send = features.ContinuityFeatures.send
    safe = features.ContinuityFeatures.safe
    educate = features.ContinuityFeatures.educate
    def test_holdout_empty_dialogue_still_supplies_durable_reports(self):
        self.send('Nisha is allergic to wool.',intent='general',memories=[dict(key='allergies',value='wool',evidence='allergic to wool')]);self.send('Keep replies detailed.',intent='general',mid=None);state,rev=self.r.store._load('a');state['history']=[];self.r.store._save('a',state,rev);out,c=self.educate('Explain rest.');context=json.loads(c.calls[0]['input'])['state'];self.assertEqual(context['history'],[]);self.assertEqual(context['members']['m']['facts']['allergies'],['wool']);self.assertTrue(any(x['key']=='answer_style' and x['value']=='detailed' for x in context['context_records']))
    def test_holdout_preference_survives_record_capacity(self):
        state={'context_records':[]};record_context(state,'Keep answers concise.',1)
        for n in range(270):record_context(state,f'Nisha report number {n}',n+2,'m')
        self.assertLessEqual(len(state['context_records']),256);self.assertTrue(any(x['key']=='answer_style' and x['status']=='active' for x in state['context_records']))
    def test_holdout_code_change_invalidates_cache(self):
        self.educate();self.r.cache.implementation='different-policy-build';out,_=self.educate();self.assertEqual(out['trace']['cache']['status'],'miss')
    def test_holdout_clinical_home_never_cached(self):
        self.safe();out=self.safe();self.assertEqual(out['route'],'home');self.assertEqual(out['trace']['cache']['status'],'bypass')
    def test_holdout_denied_memory_is_not_inserted(self):
        self.send('Nisha is not allergic to wool.',intent='general',memories=[dict(key='allergies',value='wool',evidence='not allergic to wool')]);self.assertNotIn('wool',self.r.state('a')['members']['m']['facts'].get('allergies',[]))
    def test_holdout_refusal_after_warm_cache_cannot_skip_intake(self):
        self.educate();self.r.client=ScriptedClient({'status':'completed','output':[{'type':'message','content':[{'type':'refusal','refusal':'Refused'}]}]});out=self.r.chat('a','Explain hydration.');self.assertEqual(out['route'],'error');self.assertNotEqual(out['trace']['cache']['status'],'hit')
    def test_holdout_same_labels_do_not_share_household_cache(self):
        self.educate();out,_=self.educate(household='identical-seeded-family');self.assertEqual(out['trace']['cache']['status'],'miss')
    def test_holdout_positive_report_after_negated_report_is_not_hidden(self):
        self.safe();out,_=self.send('Nisha is not getting worse, but she is short of breath.');self.assertEqual(out['route'],'doctor')

class HoldoutReminders(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.now=utc_due('2026-10-24','23:00','UTC');self.r=Reminders(Path(self.tmp.name)/'qa.db',lambda:self.now);self.addCleanup(self.r.close)
    def test_six_am_tomorrow_uses_user_zone_date(self):
        out=command(self.r,'qa','Remind me at 6 a.m. tomorrow in Asia/Kolkata to take fictional medicine.',self.now,True);rows=self.r.list('qa');self.assertEqual(len(rows),1);self.assertEqual(rows[0]['day'],'2026-10-26');self.assertEqual(rows[0]['local_time'],'06:00');self.assertIn('pending',out)
    def test_create_old_time_after_edit_does_not_reuse_edited_time(self):
        a=self.r.create('qa','Fictional','2026-10-26','06:00','UTC');self.r.edit('qa',a['id'],'2026-10-26','07:00','UTC');b=self.r.create('qa','Fictional','2026-10-26','06:00','UTC');self.assertNotEqual(a['id'],b['id']);self.assertEqual(b['local_time'],'06:00')
    def test_duplicate_target_edit_is_refused_atomically(self):
        a=self.r.create('qa','Fictional','2026-10-26','06:00','UTC');b=self.r.create('qa','Fictional','2026-10-26','07:00','UTC')
        with self.assertRaises(ReminderError):self.r.edit('qa',a['id'],'2026-10-26','07:00','UTC')
        self.assertEqual(self.r.get('qa',a['id'])['local_time'],'06:00')
    def test_worker_receipts_survive_restart_and_do_not_duplicate(self):
        a=self.r.create('qa','Fictional','2026-10-26','06:00','UTC');self.now=a['due'];self.r.tick();path=Path(self.tmp.name)/'qa.db';self.r.close();self.r=Reminders(path,lambda:self.now);self.assertEqual(self.r.tick(),0);self.assertEqual(len(self.r.receipts('qa')),1)
    def test_natural_edit_and_cancel_are_household_scoped(self):
        a=self.r.create('qa','Fictional','2026-10-26','06:00','UTC');command(self.r,'qa',f"Edit reminder {a['id']} to 7 a.m. on 2026-10-26 in UTC",self.now,True);self.assertEqual(self.r.get('qa',a['id'])['local_time'],'07:00');command(self.r,'qa','Cancel reminder '+a['id'],self.now,True);self.now=a['due']+99999;self.assertEqual(self.r.tick(),0)
