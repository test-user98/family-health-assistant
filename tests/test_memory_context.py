"""Known-fact retrieval tests: deterministic scope/budgets/provenance, not NLP quality."""
import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from memory_context import retrieve,ContextOverflow,encoded
from health_agent import CONFIG_PATH,Agent
from runtime import Runtime
from conversation import delete_note
from tests.test_runtime import ScriptedClient,envelope,intake

class MemoryContextTests(unittest.TestCase):
    def setUp(self):
        self.cfg=json.loads(CONFIG_PATH.read_text());self.cfg['members']={}
        self.state=dict(members={'M1':dict(name='Mira',relationship='mother',aliases=['mother'],age_months=636,pronouns=['she','her'],facts={'allergies':['latex']},fact_records=[dict(key='allergies',value='latex',evidence='Mira is allergic to latex.',at=1,status='active')]),'M2':dict(name='Dev',relationship='brother',aliases=['brother'],age_months=576,facts={'medications':['OTHER_MEMBER_CANARY']})},episodes={},active_member_id='M1',active_episode_id=None,context_records=[],history=[])
    def record(self,id,key,value,evidence,mid='M1',eid=None,status='active',certainty='reported'):
        return dict(id=id,key=key,value=value,evidence=evidence,member_id=mid,episode_id=eid,status=status,certainty=certainty,at=10)
    def episode(self,eid='E1',topic='headache',status='open'):
        self.state['episodes'][eid]=dict(member_id='M1',topic=topic,status=status,observations={'red_flags':dict(value=False,evidence='No',at=1)},updated_at=1);self.state['active_episode_id']=eid
    def test_default_and_configurable_memory_budgets(self):
        self.state['history']=[dict(user='Turn '+str(i),assistant='ok') for i in range(1000)]
        for turns in (0,2,6):
            cfg=copy.deepcopy(self.cfg);cfg['context']['recent_turns']=turns;r=retrieve(self.state,'Mira allergies',cfg)
            self.assertLessEqual(len(r['history']),turns);self.assertLessEqual(len(encoded(r)),3000);self.assertLessEqual(r['memory_manifest']['facts'],20)
        self.assertEqual(len(self.state['history']),1000)
    def test_member_first_retrieval_retains_safety_and_roster_only_for_others(self):
        r=retrieve(self.state,'What allergy does Mira have?',self.cfg)
        self.assertEqual(r['members']['M1']['facts']['allergies'],['latex']);self.assertEqual(r['members']['M2']['name'],'Dev');self.assertNotIn('OTHER_MEMBER_CANARY',json.dumps(r));self.assertIn('Mira is allergic to latex.',json.dumps(r))
    def test_explicit_member_switch_rebinds_projection_not_durable_state(self):
        r=retrieve(self.state,'What is Dev taking?',self.cfg);self.assertEqual(r['active_member_id'],'M2');self.assertEqual(r['members']['M2']['facts']['medications'],['OTHER_MEMBER_CANARY']);self.assertEqual(self.state['active_member_id'],'M1')
    def test_positive_negative_and_unknown_are_distinct(self):
        self.episode();r=retrieve(self.state,'Mira headache',self.cfg);e=r['episodes']['E1'];self.assertIs(e['observations']['red_flags']['value'],False);self.assertIn('duration_hours',e['unknown_fields']);self.assertNotIn('duration_hours',e['observations'])
    def test_current_correction_uncertainty_and_source_provenance(self):
        self.state['context_records']=[self.record('old','ankle_side','left','left ankle',status='superseded'),self.record('new','ankle_side','right','Correction: right ankle'),self.record('hyp','reported_context','Dust might be a cause.','Dust might be a cause.',certainty='user_hypothesis')]
        r=retrieve(self.state,'Mira ankle',self.cfg);text=json.dumps(r);self.assertNotIn('left ankle',text);self.assertIn('right ankle',text);self.assertIn('user_hypothesis',text);self.assertIn('new',text)
    def test_duplicate_raw_assertions_consolidate_without_new_inferences(self):
        self.state['context_records']=[self.record('a','reported_context','Maybe dust.','Maybe dust.',certainty='user_hypothesis'),self.record('b','reported_context','Maybe dust.','Maybe dust.',certainty='user_hypothesis')]
        r=retrieve(self.state,'Mira cough',self.cfg);self.assertEqual(len(r['context_records']),1);self.assertEqual(r['context_records'][0]['certainty'],'user_hypothesis');self.assertEqual(len(self.state['context_records']),2)
    def test_relevant_uncertain_fact_outranks_newer_unrelated_chatter(self):
        self.state['context_records']=[self.record('relevant','reported_context','Dust might trigger cough.','Dust might trigger cough.',certainty='user_hypothesis')]+[{**self.record(str(i),'reported_context','Unrelated books '+str(i),'Unrelated books '+str(i)), 'at':100+i} for i in range(50)]
        cfg=copy.deepcopy(self.cfg);cfg['context']['max_facts']=2
        r=retrieve(self.state,'Mira cough cause',cfg);self.assertEqual(r['context_records'][0]['id'],'relevant');self.assertEqual(r['context_records'][0]['certainty'],'user_hypothesis')
    def test_conflicting_active_values_are_not_resolved_by_frequency(self):
        self.state['context_records']=[self.record('a','ankle_side','right','right ankle'),self.record('b','ankle_side','left','left ankle')]
        r=retrieve(self.state,'Mira ankle',self.cfg);self.assertEqual({x['value'] for x in r['context_records']},{'right','left'})
    def test_scope_filters_and_global_preference_evidence_redaction(self):
        self.state['context_records']=[self.record('pref','answer_style','concise','OTHER_MEMBER_CANARY. Keep answers concise.',mid=None)]
        r=retrieve(self.state,'Mira',self.cfg,{'M1'});self.assertNotIn('OTHER_MEMBER_CANARY',json.dumps(r));self.assertNotIn('M2',r['members']);self.assertIn('concise',json.dumps(r))
    def test_critical_overflow_refuses_instead_of_dropping_allergy(self):
        cfg=copy.deepcopy(self.cfg);cfg['context']['max_facts']=0
        with self.assertRaises(ContextOverflow):retrieve(self.state,'Mira',cfg)
        self.assertEqual(self.state['members']['M1']['facts']['allergies'],['latex'])
    def test_tombstone_and_old_summary_cannot_resurrect_deleted_note(self):
        text='Mira has a right ankle injury.';self.state['context_records']=[self.record('x','reported_context',text,text)];self.state['summary']=text;self.state['deleted_evidence']=[hashlib.sha256(text.encode()).hexdigest()]
        r=retrieve(self.state,'Mira',self.cfg);self.assertNotIn('right ankle',json.dumps(r));self.assertNotIn('summary',r)
    def test_relevant_past_episode_retained_without_other_topic_history(self):
        self.episode('active','cough');self.episode('past','headache','closed');self.state['active_episode_id']='active'
        self.state['episodes']['irrelevant']={'member_id':'M1','topic':'stomach','status':'closed','observations':{},'updated_at':2}
        r=retrieve(self.state,'Mira previous headache',self.cfg);self.assertIn('active',r['episodes']);self.assertIn('past',r['episodes']);self.assertNotIn('irrelevant',r['episodes'])
    def test_validated_update_persists_when_response_context_overflows(self):
        cfg=copy.deepcopy(self.cfg);cfg['members']=copy.deepcopy(self.state['members']);cfg['context']['max_facts']=1
        c=ScriptedClient(envelope(intake(member_id='M1',memories=[dict(key='allergies',value='peanut',evidence='allergic to peanut')])))
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'state.db',c,config=cfg)
            try:
                out=r.chat('A','Mira is allergic to peanut.');self.assertEqual(out['route'],'context_overflow');self.assertTrue(out['trace']['validated_updates_saved']);self.assertEqual(len(c.calls),1);self.assertEqual(set(r.state('A')['members']['M1']['facts']['allergies']),{'latex','peanut'})
            finally:r.close()
    def test_emergency_bypasses_even_impossible_context_budget(self):
        cfg=copy.deepcopy(self.cfg);cfg['members']=copy.deepcopy(self.state['members']);cfg['context']['max_facts']=0
        c=ScriptedClient()
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'state.db',c,config=cfg)
            try:self.assertEqual(r.chat('A','Mira cannot breathe.')['route'],'emergency');self.assertEqual(c.calls,[])
            finally:r.close()
    def test_adaptive_question_selects_missing_parameter_only(self):
        from question_selection import choose
        cfg=copy.deepcopy(self.cfg);cfg['question_selection']={'mode':'adaptive'}
        self.assertEqual(choose(['red_flags','duration_hours','hydration_ok'],'stomach',cfg),'red_flags')
        self.assertEqual(choose(['duration_hours','hydration_ok'],'stomach',cfg),'hydration_ok')
        self.assertEqual(choose(['duration_hours','severity'],'headache',cfg),'severity')
        self.assertEqual(choose(['duration_hours','severity'],'headache',self.cfg),'duration_hours')
        cfg['question_selection']['topic_priorities']={'stomach':['invented','hydration_ok']}
        self.assertEqual(choose(['duration_hours'],'stomach',cfg),'duration_hours')
    def test_actual_runtime_adaptive_answer_binding_and_unchanged_thresholds(self):
        cfg=copy.deepcopy(self.cfg);cfg['members']=copy.deepcopy(self.state['members']);cfg['question_selection']={'mode':'adaptive'};rules=copy.deepcopy(cfg['doctor_rules'])
        c=ScriptedClient(envelope(intake(member_id='M1',intent='symptom',topic='stomach',observations=[dict(key='red_flags',value=False,evidence='no red flags')])),envelope(intake(member_id='M1',intent='symptom',topic='stomach',observations=[dict(key='hydration_ok',value=False,evidence='No')])))
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'state.db',c,config=cfg)
            try:
                out=r.chat('A','Mira has stomach symptoms, no red flags.');self.assertEqual(out['route'],'ask');self.assertEqual(r.state('A')['question_key'],'hydration_ok');review=r.chat('A','No');self.assertEqual(review['route'],'doctor');self.assertEqual(review['message'],cfg['messages']['doctor']);self.assertEqual(r.config['doctor_rules'],rules)
            finally:r.close()
