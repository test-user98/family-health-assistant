"""Holdout regressions for continuity, extraction conflict and state-aware cache."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from runtime import Runtime
from health_agent import CONFIG_PATH
from tests.test_runtime import ScriptedClient,envelope,intake

class ContinuityFeatures(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.now=10000
        self.cfg=json.loads(CONFIG_PATH.read_text());self.cfg['members']={
          'm':dict(name='Nisha',relationship='mother',name_confirmed=True,age_months=600,aliases=['mother'],pronouns=['she','her'],facts={}),
          's':dict(name='Leela',relationship='sister',name_confirmed=True,age_months=240,aliases=['sister'],pronouns=['she','her'],facts={})}
        self.path=Path(self.tmp.name)/'synthetic.db';self.r=Runtime(self.path,None,config=self.cfg,clock=lambda:self.now);self.addCleanup(lambda:self.r.close())
    def send(self,text,obs=None,mid='m',intent='symptom',memories=None):
        c=ScriptedClient(envelope(intake(member_id=mid,intent=intent,topic='headache',observations=[dict(key=k,value=v,evidence=text) for k,v in (obs or {}).items()],memories=memories or [])),envelope({'message':'Fictional educational reply.','source_ids':[]}));self.r.client=c
        return self.r.chat('a',text),c
    def safe(self):return self.send('Nisha has mild headache for 3 hours, no red flags, drinking normally.',{'severity':'mild','duration_hours':3,'red_flags':False,'hydration_ok':True})[0]
    def educate(self,text='Explain hydration.',household='a'):
        c=ScriptedClient(envelope(intake()),envelope({'message':'Fictional educational reply.','source_ids':[]}));self.r.client=c;return self.r.chat(household,text),c
    def test_deterioration_omission_cannot_reuse_reassurance(self):
        self.safe();out,_=self.send('Nisha is getting worse.');self.assertEqual(out['route'],'doctor')
    def test_negated_hydration_cannot_validate_positive(self):
        self.safe();out,_=self.send('Nisha is not drinking normally.',{'hydration_ok':True});self.assertEqual(out['route'],'doctor')
    def test_omission_guard_cannot_suppress_model_emergency(self):
        self.r.client=ScriptedClient(envelope(intake(review='emergency',review_evidence='breathless')));self.assertEqual(self.r.chat('a','Nisha is breathless.')['route'],'emergency')
    def test_deterioration_plus_provider_failure_keeps_review_hold(self):
        self.safe();self.r.client=ScriptedClient(TimeoutError());self.assertEqual(self.r.chat('a','Nisha is getting worse.')['route'],'doctor');self.assertEqual(self.safe()['route'],'doctor')
    def test_not_only_is_not_a_denial(self):
        self.safe();out,_=self.send('Nisha is not only getting worse, she feels unwell.');self.assertEqual(out['route'],'doctor')
    def test_negated_worsening_is_not_a_positive_report(self):
        self.safe();out,_=self.send('Nisha is not getting worse.');self.assertEqual(out['route'],'home')
    def test_hypothetical_worsening_is_educational(self):
        out,_=self.educate('What if Nisha is getting worse?');self.assertEqual(out['route'],'general')
    def test_medical_correction_retracts_only_subject_fact_and_keeps_provenance(self):
        self.send('Nisha is allergic to latex.',intent='general',memories=[dict(key='allergies',value='latex',evidence='allergic to latex')]);self.send('Nisha is not allergic to latex; she is allergic to wool.',intent='general',memories=[dict(key='allergies',value='wool',evidence='allergic to wool')]);m=self.r.state('a')['members']['m'];self.assertEqual(m['facts']['allergies'],['wool']);self.assertTrue(any(x['value']=='latex' and x['status']=='retracted' for x in m['fact_records']));self.assertEqual(self.r.state('a')['members']['s']['facts'],{})
    def test_do_not_forget_is_not_fact_retraction(self):
        self.send('Nisha is allergic to latex.',intent='general',memories=[dict(key='allergies',value='latex',evidence='allergic to latex')]);self.send('Do not forget Nisha latex allergy.',intent='general');self.assertEqual(self.r.state('a')['members']['m']['facts']['allergies'],['latex'])
    def test_not_only_allergy_keeps_existing_fact(self):
        self.send('Nisha is allergic to latex.',intent='general',memories=[dict(key='allergies',value='latex',evidence='allergic to latex')]);self.send('Nisha is allergic to not only latex but also wool.',intent='general',memories=[dict(key='allergies',value='wool',evidence='also wool')]);self.assertEqual(self.r.state('a')['members']['m']['facts']['allergies'],['latex','wool'])
    def test_same_gender_pronoun_cannot_silently_switch_active_person(self):
        self.safe();out,_=self.send('She has headache for 8 hours.',mid='s',obs={'duration_hours':8});self.assertEqual(out['route'],'clarify_member');self.assertFalse(any(e['member_id']=='s' for e in self.r.state('a')['episodes'].values()))
    def test_retrospective_report_does_not_reset_current_duration(self):
        a=self.safe();self.send('Yesterday Nisha had headache for 1 hour.',{'duration_hours':1});self.assertEqual(self.r.state('a')['episodes'][a['episode_id']]['observations']['duration_hours']['value'],3)
    def test_preference_and_laterality_supersession_are_evidence_linked(self):
        self.send('Please keep answers brief.',intent='general',mid=None);self.send('Nisha has left wrist pain.');self.send('Correction: Nisha has right wrist pain, not left.');rows=self.r.state('a')['context_records'];self.assertTrue(any(x['key']=='answer_style' and x['status']=='active' for x in rows));self.assertTrue(any(x['key']=='wrist_side' and x['value']=='right' and x['status']=='active' and x['evidence'] for x in rows));self.assertFalse(any(x['key']=='wrist_side' and x['value']=='left' and x['status']=='active' for x in rows))
    def test_cache_hit_has_one_intake_call_and_no_response_call(self):
        a,c1=self.educate();b,c2=self.educate();self.assertEqual(a['trace']['cache']['status'],'miss');self.assertEqual(b['trace']['cache']['status'],'hit');self.assertEqual(len(c1.calls),2);self.assertEqual(len(c2.calls),1)
    def test_cache_is_household_scoped(self):
        self.educate();b,c=self.educate(household='b');self.assertEqual(b['trace']['cache']['status'],'miss');self.assertEqual(len(c.calls),2)
    def test_cache_expires(self):
        self.educate();self.now+=61;b,_=self.educate();self.assertEqual(b['trace']['cache']['status'],'miss')
    def test_cache_invalidates_after_fact_correction(self):
        self.educate();self.send('Nisha is allergic to latex.',intent='general',memories=[dict(key='allergies',value='latex',evidence='allergic to latex')]);b,_=self.educate();self.assertEqual(b['trace']['cache']['status'],'miss')
    def test_cache_invalidates_after_member_switch(self):
        self.educate();self.send('Leela',mid='s',intent='select');b,_=self.educate();self.assertEqual(b['trace']['cache']['status'],'miss')
    def test_cache_invalidates_after_model_policy_or_prompt_change(self):
        for change in ['model','policy','prompt']:
            with self.subTest(change=change):
                self.educate()
                if change=='model':self.r.models['conversation']['effort']='high'
                elif change=='policy':self.r.config['freshness_hours']+=1
                else:
                    with patch('runtime.RESPONSE_PROMPT','Changed prompt'):
                        b,_=self.educate();self.assertEqual(b['trace']['cache']['status'],'miss');continue
                b,_=self.educate();self.assertEqual(b['trace']['cache']['status'],'miss')
    def test_emergency_worsening_never_uses_cached_reply(self):
        self.educate();self.r.client=ScriptedClient();out=self.r.chat('a','Nisha cannot breathe.');self.assertEqual(out['route'],'emergency');self.assertEqual(out['trace']['cache']['status'],'bypass');self.assertEqual(out['trace']['calls'],[])
    def test_cache_persists_across_runtime_restart(self):
        self.educate();self.r.close();self.r=Runtime(self.path,None,config=self.cfg,clock=lambda:self.now);b,_=self.educate();self.assertEqual(b['trace']['cache']['status'],'hit')
    def test_usage_metadata_is_allowlisted(self):
        c=ScriptedClient(dict(envelope(intake()),usage={'input_tokens':30,'output_tokens':10,'input_tokens_details':{'cached_tokens':20},'output_tokens_details':{'reasoning_tokens':5},'secret':'DO_NOT_COPY'}),envelope({'message':'Okay.','source_ids':[]}));self.r.client=c;out=self.r.chat('a','Explain rest.');u=out['trace']['calls'][0]['usage'];self.assertEqual(u['cached_tokens'],20);self.assertEqual(u['reasoning_tokens'],5);self.assertNotIn('secret',u)
