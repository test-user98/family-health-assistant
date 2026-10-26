"""Offline causal fixtures and actual loopback HTTP; never live model evidence."""
import copy,http.client,json,tempfile,threading,unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from runtime import Runtime
from health_agent import CONFIG_PATH
from server import handler_for
from tests.test_runtime import ScriptedClient,envelope,intake
from qa_20261025.model_benchmark import seed,judge
from qa_20261025.stage_trace import SyntheticTrace

class HarnessAuditTests(unittest.TestCase):
    def config(self):
        cfg=json.loads(CONFIG_PATH.read_text());cfg['members']={}
        for key in ('learning','human_review','reminders'):cfg[key]={'enabled':False}
        return cfg

    def fixture(self,case):
        if case=='unknown':data=intake(member_id='M2',intent='select',observations=[dict(key='temperature_c',value='unknown',evidence='known')]);text="Is Dev's temperature known? Say unknown if it is not recorded.";reply='Dev temperature is unknown, not measured.'
        elif case=='age':data=intake(member_id='M1',observations=[dict(key='age_months',value=636,evidence='Mira')]);text='How old is Mira?';reply='Mira is 53 years old.'
        elif case=='allergy':data=intake(member_id='M1',intent='select',memories=[dict(key='allergies',value='latex',evidence='Mira')]);text='What allergy was reported for Mira?';reply='The reported allergy for Mira is latex.'
        else:data=intake(member_id='M1',episode_id='old_headache',intent='select',topic='headache');text="What is the status of Mira's older headache episode?";reply='Her older headache episode is closed.'
        return ScriptedClient(envelope(data),envelope({'message':reply,'source_ids':[],'citations':[]})),text

    def test_old_normalization_reproduces_unknown_rejection_and_current_preserves_storage(self):
        with tempfile.TemporaryDirectory() as d:
            for before in (True,False):
                client,text=self.fixture('unknown');r=Runtime(Path(d)/str(before),client,config=self.config());seed(r)
                try:
                    initial=copy.deepcopy(r.state('benchmark-synthetic'))
                    with patch('runtime.readback_target',return_value=None) if before else patch('runtime.readback_target',wraps=__import__('memory_queries').target):answer=r.chat('benchmark-synthetic',text)
                    self.assertEqual(answer['route'],'error' if before else 'general')
                    if before:
                        self.assertEqual(answer['trace']['failure_stage'],'observation_validation')
                        self.assertEqual(answer['trace']['failure']['validator_reason'],'invalid measurement')
                    else:
                        self.assertEqual(r.state('benchmark-synthetic')['members'],initial['members'])
                        self.assertEqual(r.state('benchmark-synthetic')['episodes'],initial['episodes'])
                        self.assertEqual(len(client.calls),2)
                        sent=json.loads(client.calls[0]['input'])['state'];self.assertIn('M2',sent['members']);self.assertNotIn('temperature_c',json.dumps(sent['members']['M2']))
                finally:r.close()

    def test_resolved_select_old_route_is_behaviorally_correct_but_strict_route_differs(self):
        client,text=self.fixture('episode')
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'test.db',client,config=self.config());seed(r)
            try:
                with patch('runtime.readback_target',return_value=None):answer=r.chat('benchmark-synthetic',text)
                self.assertEqual(answer['route'],'select');self.assertIn('closed',answer['message'])
                self.assertEqual(judge('episode',answer,r'closed|resolved','general',r.state('benchmark-synthetic')),(True,False))
                self.assertEqual(r.state('benchmark-synthetic')['episodes']['old_headache']['status'],'closed')
            finally:r.close()

    def test_actual_http_readbacks_include_context_and_do_not_mutate_facts(self):
        cfg=self.config();events=[];clients=[]
        class Settings:
            def metadata(self,session):return {'configured':True,'provider':'offline fixture'}
            def engine(settings,session,path):
                case=events[-1];client,_=self.fixture(case);clients.append(client)
                r=Runtime(path,client,config=cfg);seed(r);state=r.state('benchmark-synthetic');r.store._save(session,state,None)
                return r
        with tempfile.TemporaryDirectory() as d:
            server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(Path(d)/'http.db',config=cfg,settings=Settings()))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                for case in ('age','allergy','unknown','episode'):
                    events.append(case);_,text=self.fixture(case);conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                    conn.request('POST','/api/chat',json.dumps({'message':text}),{'Content-Type':'application/json'})
                    response=conn.getresponse();answer=json.loads(response.read());conn.close()
                    self.assertEqual(response.status,200);self.assertEqual(answer['route'],'general',case)
                    self.assertIn('read_only_memory_query',answer['trace']['guardrails']);self.assertEqual(len(clients[-1].calls),2)
                    request=json.loads(clients[-1].calls[0]['input']);self.assertIn('636',json.dumps(request))
                    if case=='unknown':
                        self.assertEqual(request['state']['memory_manifest']['selected_members'],['M2'])
                        self.assertNotIn('latex',json.dumps(request));self.assertEqual(request['state']['episodes'],{})
                    else:self.assertIn('latex',json.dumps(request));self.assertIn('old_headache',json.dumps(request))
                    self.assertEqual(answer['trace']['tools'],[])
            finally:server.shutdown();server.server_close();thread.join()

    def test_oracle_does_not_accept_wrong_episode_or_current_active_claim(self):
        for member,episode,text in [('M2','old_headache','closed'),('M1','other','closed'),('M1','old_headache','It is active but was closed before.')]:
            self.assertFalse(judge('episode',dict(route='select',member_id=member,episode_id=episode,message=text),r'closed|resolved','general')[0])

    def test_unbound_readback_fixture_rejection_is_grounded_without_claiming_live_cause(self):
        # A causal fixture, NOT a reconstruction of the missing live extraction.
        for text,reply in [('How old is Mira?','Mira is 53.'),('What allergy was reported for Mira?','Mira reported latex.')]:
            with tempfile.TemporaryDirectory() as d:
                for before in (True,False):
                    client=ScriptedClient(envelope(intake(member_id=None)),envelope({'message':reply,'source_ids':[]}))
                    r=Runtime(Path(d)/str(before),client,config=self.config());seed(r)
                    try:
                        with patch('runtime.readback_target',return_value=None) if before else patch('runtime.readback_target',wraps=__import__('memory_queries').target):answer=r.chat('benchmark-synthetic',text)
                        self.assertEqual(answer['route'],'error' if before else 'general')
                        if before:self.assertEqual(answer['trace']['failure_stage'],'observation_validation')
                        else:self.assertEqual(answer['member_id'],'M1')
                    finally:r.close()

    def test_unsupported_citation_still_rejected_for_readback(self):
        client=ScriptedClient(envelope(intake(member_id='M1')),envelope({'message':'Latex [999]','source_ids':[999]}))
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'db',client,config=self.config());seed(r)
            try:
                answer=r.chat('benchmark-synthetic','What allergy was reported for Mira?')
                self.assertIn('could not substantiate',answer['message']);self.assertEqual(answer['sources'],[])
                self.assertEqual(r.state('benchmark-synthetic')['members']['M1']['facts']['allergies'],['latex'])
            finally:r.close()

    def test_factually_matching_general_answer_for_wrong_member_fails_oracle(self):
        self.assertEqual(judge('age',dict(route='general',message='53',member_id='M2'),r'\b53\b','general'),(False,False))
        for case,text,pattern,member in [('age','not 53',r'\b53\b','M1'),('allergy','not latex',r'latex','M1'),('unknown','not unknown',r'unknown','M2'),('episode','not closed',r'closed','M1')]:
            self.assertEqual(judge(case,dict(route='general',message=text,member_id=member),pattern,'general'),(False,False))

    def test_provider_exception_body_is_not_in_runtime_failure_trace(self):
        c=ScriptedClient(ValueError('token-private-canary'))
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'db',c,config=self.config());seed(r)
            try:
                answer=r.chat('benchmark-synthetic','How old is Mira?')
                self.assertNotIn('token-private-canary',json.dumps(answer));self.assertEqual(answer['trace']['failure']['exception_type'],'ValueError');self.assertIsNone(answer['trace']['failure']['validator_reason'])
            finally:r.close()

    def test_actual_live_preference_extraction_does_not_enter_medical_memory(self):
        text='Please keep your answers concise throughout our conversation.'
        data=intake(memories=[dict(key='answer_style',value='concise',evidence=text)])
        client=ScriptedClient(envelope(data),envelope({'message':'I will keep replies concise.','source_ids':[]}))
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'db',client,config=self.config())
            try:
                answer=r.chat('A',text);self.assertEqual(answer['route'],'general')
                state=r.state('A');self.assertEqual(state['members'],{});self.assertEqual(state['episodes'],{})
                self.assertTrue(any(x.get('key')=='answer_style' and x.get('value')=='concise' for x in state['context_records']))
                self.assertIn('household_preference_stored_separately',answer['trace']['guardrails'])
                schema=client.calls[0]['schema'];self.assertEqual(schema['properties']['memories']['items']['properties']['key']['enum'],['allergies','conditions','medications'])
                self.assertNotIn('age_months',str(schema['properties']['observations']['items']))
            finally:r.close()

    def test_actual_live_cough_quote_terminal_punctuation_can_be_grounded(self):
        from tests.test_runtime import profile
        text='Add my brother Arjun, age 28. He has a mild cough since yesterday.'
        data=intake(intent='register',topic='cough',observations=[dict(key='severity',value='mild',evidence='He has a mild cough.'),dict(key='duration_hours',value=24,evidence='since yesterday')],profile_updates=[profile(name='Arjun',age=336,relationship='brother',name_evidence='Add my brother Arjun',age_evidence='age 28')])
        client=ScriptedClient(envelope(data))
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'db',client,config=self.config())
            try:
                answer=r.chat('A',text);self.assertEqual(answer['route'],'ask');s=r.state('A');episode=s['episodes'][answer['episode_id']]
                self.assertEqual(episode['observations']['severity']['evidence'],'He has a mild cough')
                self.assertEqual(s['members'][answer['member_id']]['name'],'Arjun')
                self.assertIn('literal_quote_terminal_punctuation_normalized',answer['trace']['guardrails'])
            finally:r.close()

    def test_quote_repair_does_not_remove_internal_negation_or_accept_paraphrases(self):
        for evidence in ['She feels mildly unwell.','She has fever.']:
            c=ScriptedClient(envelope(intake(member_id='M1',intent='symptom',topic='cough',observations=[dict(key='severity',value='mild',evidence=evidence)])))
            with tempfile.TemporaryDirectory() as d:
                r=Runtime(Path(d)/'db',c,config=self.config());seed(r)
                try:
                    answer=r.chat('benchmark-synthetic','Mira has no fever and a mild cough.')
                    self.assertEqual(answer['route'],'error');self.assertEqual(answer['trace']['failure']['validator_reason'],'duplicate key or ungrounded evidence')
                finally:r.close()

    def test_actual_live_unknown_temperature_is_a_valid_unavailable_clinical_field(self):
        text='Mira has no red flags and is drinking and urinating normally. I do not know her temperature.'
        c=ScriptedClient(envelope(intake(member_id='M1',episode_id='current_cough',intent='symptom',topic='cough',observations=[dict(key='red_flags',value=False,evidence='no red flags'),dict(key='hydration_ok',value=True,evidence='drinking and urinating normally.')],unavailable_fields=['temperature_c'])))
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db';r=Runtime(path,c,config=self.config());seed(r)
            try:
                answer=r.chat('benchmark-synthetic',text);self.assertEqual(answer['route'],'home')
                episode=r.state('benchmark-synthetic')['episodes']['current_cough'];self.assertNotIn('temperature_c',episode['observations'])
                self.assertEqual(episode['unavailable_records']['temperature_c']['status'],'reported_unknown')
                self.assertIn('do not know',episode['unavailable_records']['temperature_c']['evidence'])
            finally:r.close()
            r=Runtime(path,None,config=self.config())
            try:
                from memory_context import retrieve
                context=retrieve(r.state('benchmark-synthetic'),'How is Mira?',self.config())
                self.assertEqual(context['episodes']['current_cough']['unavailable_records']['temperature_c']['status'],'reported_unknown')
            finally:r.close()

    def test_resolution_and_other_known_episode_are_processed_separately(self):
        c=ScriptedClient(envelope(intake(member_id='M1',episode_id='head',intent='resolve',topic='headache',observations=[dict(key='severity',value='resolved',evidence='headache has now completely resolved')])),envelope(intake(member_id='M1',episode_id='ankle',intent='symptom',topic='ankle pain')))
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'db',c,config=self.config());seed(r);s,rev=r.store._load('benchmark-synthetic')
            e=copy.deepcopy(s['episodes']['current_cough']);s['episodes']={'head':{**copy.deepcopy(e),'topic':'headache'},'ankle':{**e,'topic':'ankle pain','body_site':None}};s['active_episode_id']='ankle';r.store._save('benchmark-synthetic',s,rev)
            try:
                answer=r.chat('benchmark-synthetic',"Mira's headache has now completely resolved. Her ankle is still sore.")
                self.assertEqual(answer['route'],'multi_episode');self.assertEqual([x['route'] for x in answer['parts']],['resolved','home'])
                saved=r.state('benchmark-synthetic');self.assertEqual(saved['episodes']['head']['status'],'closed');self.assertEqual(saved['episodes']['ankle']['status'],'open')
                self.assertEqual(len(c.calls),2);self.assertNotIn('ankle is still sore',json.loads(c.calls[0]['input'])['message'])
            finally:r.close()

    def test_resolved_severity_normalization_never_downgrades_model_review(self):
        text='Mira headache has now completely resolved.'
        c=ScriptedClient(envelope(intake(member_id='M1',intent='resolve',topic='headache',observations=[dict(key='severity',value='resolved',evidence='headache has now completely resolved')],review='doctor',review_evidence='Mira')))
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'db',c,config=self.config());seed(r)
            try:self.assertEqual(r.chat('benchmark-synthetic',text)['route'],'doctor')
            finally:r.close()

    def test_actual_unknown_zero_sentinel_is_not_stored_as_a_measurement(self):
        text="Mira's cough is still mild. Her temperature remains unknown."
        c=ScriptedClient(envelope(intake(member_id='M1',episode_id='current_cough',intent='symptom',topic='cough',observations=[dict(key='severity',value='mild',evidence="Mira's cough is still mild"),dict(key='temperature_c',value=0,evidence='Her temperature remains unknown')],unavailable_fields=['temperature_c'])))
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'db',c,config=self.config());seed(r)
            try:
                answer=r.chat('benchmark-synthetic',text);self.assertEqual(answer['route'],'home')
                self.assertNotIn('temperature_c',r.state('benchmark-synthetic')['episodes']['current_cough']['observations'])
                self.assertIn('unknown_measurement_not_numeric',answer['trace']['guardrails'])
            finally:r.close()

    def test_laterality_correction_cannot_fabricate_negative_red_flags(self):
        text="Correction: Mira's ankle is right, not left."
        c=ScriptedClient(envelope(intake(member_id='M1',intent='symptom',topic='ankle pain',observations=[dict(key='red_flags',value=False,evidence=text)])))
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'db',c,config=self.config());seed(r)
            try:
                answer=r.chat('benchmark-synthetic',text);self.assertEqual(answer['route'],'ask')
                self.assertNotIn('red_flags',r.state('benchmark-synthetic')['episodes'][answer['episode_id']]['observations'])
                self.assertIn('unsupported_red_flag_denial_not_saved',answer['trace']['guardrails'])
            finally:r.close()

    def test_provider_schema_evidence_spans_are_literal_and_exclude_actual_paraphrase(self):
        from runtime import intake_schema
        text='Meera has no red flags, is drinking and urinating normally, and the headache started six hours ago.'
        schema=intake_schema(self.config()['topics'],source=text)
        for branch in schema['properties']['observations']['items']['anyOf']:
            choices=branch['properties']['evidence']['enum']
            self.assertTrue(all(choice in text for choice in choices));self.assertIn('is drinking and urinating normally',choices)
            self.assertNotIn('Meera is drinking and urinating normally',choices)

    def test_multiline_pending_source_does_not_create_provider_rejected_enum(self):
        from runtime import intake_schema
        text='Recall our corrected facts.\nWhat allergy was reported for Meera?'
        schema=intake_schema(self.config()['topics'],source=text)
        for branch in schema['properties']['observations']['items']['anyOf']:
            self.assertNotIn('enum',branch['properties']['evidence'])
            self.assertEqual(branch['properties']['evidence']['type'],'string')

    def test_household_recall_includes_unnamed_member_and_does_not_start_intake(self):
        from qa_20261025.live_runner import RECALL
        c=ScriptedClient(envelope(intake()),envelope({'message':'Tara headache is active; Meera headache resolved.','source_ids':[]}))
        cfg=self.config()
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'db',c,config=cfg);seed(r);s,rev=r.store._load('benchmark-synthetic')
            s['members']['M1']['name']='Meera';s['members']['M2']['name']='Arjun';s['members']['M3']={'name':'Tara','relationship':'daughter','aliases':['daughter'],'age_months':192,'facts':{}}
            s['episodes']['tara_head']={'member_id':'M3','topic':'headache','status':'open','observations':{}}
            r.store._save('benchmark-synthetic',s,rev)
            try:
                answer=r.chat('benchmark-synthetic',RECALL);self.assertEqual(answer['route'],'general');self.assertIsNone(r.state('benchmark-synthetic').get('pending_message'))
                context=json.loads(c.calls[0]['input'])['state'];self.assertIn('M3',context['memory_manifest']['selected_members']);self.assertIn('tara_head',context['episodes'])
            finally:r.close()

    def test_actual_compound_greeting_avoids_unnecessary_intake(self):
        c=ScriptedClient()
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'db',c,config=self.config());seed(r)
            try:
                answer=r.chat('benchmark-synthetic','Hello, how are you?');self.assertEqual(answer['route'],'general');self.assertEqual(c.calls,[])
                self.assertEqual(r.chat('benchmark-synthetic','Hello, how are you? Mira cannot breathe.')['route'],'emergency')
            finally:r.close()

    def test_empty_general_topic_is_not_a_clinical_validation_failure(self):
        c=ScriptedClient(envelope(intake(topic='')),envelope({'message':'Here is a short explanation.','source_ids':[]}))
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'db',c,config=self.config())
            try:self.assertEqual(r.chat('A','Explain how this conversation works.')['route'],'general')
            finally:r.close()

    def test_source_free_memory_reply_cannot_select_nonexistent_citations(self):
        c=ScriptedClient(envelope(intake(member_id='M1')),envelope({'message':'Mira is 53.','source_ids':[],'citations':[]}))
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'db',c,config=self.config());seed(r)
            try:
                answer=r.chat('benchmark-synthetic','How old is Mira?');self.assertEqual(answer['route'],'general')
                schema=c.calls[-1]['schema'];self.assertEqual(schema['properties']['source_ids']['maxItems'],0);self.assertEqual(schema['properties']['citations']['maxItems'],0)
                import jsonschema
                with self.assertRaises(jsonschema.ValidationError):jsonschema.validate({'message':'53','source_ids':[1],'citations':[]},schema)
            finally:r.close()

    def test_citation_schema_tracks_only_actual_tool_sources_without_mutating_base(self):
        from runtime import reply_schema,REPLY_SCHEMA
        import jsonschema
        original=copy.deepcopy(REPLY_SCHEMA);s=reply_schema([{'id':1},{'id':3}])
        self.assertEqual(s['properties']['source_ids']['items']['enum'],[1,3]);self.assertEqual(s['properties']['citations']['items']['properties']['source_id']['enum'],[1,3]);self.assertEqual(REPLY_SCHEMA,original)
        jsonschema.Draft202012Validator.check_schema(reply_schema([]))
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate({'message':'x','source_ids':[2],'citations':[]},s)

    def test_runtime_trace_records_both_actual_stages_with_known_retrieval(self):
        client,text=self.fixture('age')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'trace';saved=[];trace=SyntheticTrace(p,client,synthetic=True,budget_checkpoint=lambda:saved.append(True))
            r=Runtime(Path(d)/'db',trace,config=self.config());seed(r)
            try:
                answer=r.chat('benchmark-synthetic',text);self.assertEqual(answer['route'],'general')
                rows=[json.loads(line) for line in p.read_text().splitlines()]
                self.assertEqual([x['stage'] for x in rows],['intake','response']);self.assertEqual(len(saved),2)
                self.assertIn('read-only general',rows[0]['prompt']);self.assertIn('636',rows[0]['actual_input']);self.assertIsInstance(rows[0]['schema'],dict)
                self.assertEqual(rows[0]['decoded_output']['observations'][0]['key'],'age_months')
            finally:r.close()

    def test_stage_trace_budget_checkpoint_precedes_secret_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'trace.jsonl';marks=[];secret='synthetic-secret-canary'
            client=ScriptedClient(envelope({'message':secret,'source_ids':[]}))
            trace=SyntheticTrace(path,client,synthetic=True,secrets=(secret,),budget_checkpoint=lambda:marks.append('saved'))
            with self.assertRaises(ValueError):trace(stage='response',input='synthetic',instructions='prompt',schema={},tools=[])
            self.assertEqual(marks,['saved']);self.assertFalse(path.exists())

    def test_stage_trace_records_actual_prompt_schema_data_and_omits_raw_exceptions(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'trace';client=ScriptedClient(envelope(intake()),RuntimeError('private-body-canary'))
            trace=SyntheticTrace(p,client,synthetic=True,secrets=('secret-canary',))
            trace(stage='intake',input='synthetic',instructions='actual prompt',schema={'type':'object'},tools=[])
            with self.assertRaises(RuntimeError):trace(stage='response',input='synthetic')
            saved=p.read_text();rows=[json.loads(x) for x in saved.splitlines()]
            self.assertEqual(rows[0]['prompt'],'actual prompt');self.assertEqual(rows[0]['schema'],{'type':'object'});self.assertIn('decoded_output',rows[0]);self.assertNotIn('private-body-canary',saved)
            with self.assertRaises(ValueError):SyntheticTrace(p,client)

    def test_trace_size_limit_is_explicit_and_pre_dispatch_secret_input_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'trace';client=ScriptedClient(envelope(intake()))
            trace=SyntheticTrace(p,client,synthetic=True,secrets=('canary',),max_bytes=10)
            with self.assertRaises(ValueError):trace(stage='intake',input='canary')
            self.assertEqual(client.calls,[])
            with self.assertRaises(ValueError):trace(stage='intake',input='safe')
            self.assertFalse(p.exists())
