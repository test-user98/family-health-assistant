"""New runner dry-run checks: fake transports only, never a key or paid optimization."""
import io,json,pathlib,tempfile,unittest
from unittest.mock import patch
import live_learning as ll
from live_runner import contains_secret

class LiveLearningTests(unittest.TestCase):
    def test_exported_accounting_floor_survives_missing_private_receipts(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d);q=root/'qa';q.mkdir()
            (root/'accounting_floor.json').write_text(json.dumps({'maximum_accounted_usd':'2.30','private_history_anchor':'historical'}))
            p=q/'live_new';p.mkdir();(p/'result.json').write_text(json.dumps({'evidence':'actual_live_provider','prior_accounted_usd':'2.30','budget':{'attempts':1,'maximum_accounted_usd':'2.40'}}))
            with patch.object(ll,'ROOT',q):
                self.assertEqual(ll.previous_accounting(cap=ll.Decimal('5'))[0],ll.Decimal('2.40'))
                with self.assertRaises(ll.StopQA):ll.previous_accounting(cap=ll.Decimal('1'))
    def test_project_env_file_is_explicit_and_not_copied_to_environment(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            q=pathlib.Path(d)/'qa';q.mkdir();env=pathlib.Path(d)/'.env';env.write_text('OPENROUTER_API_KEY="SYNTHETIC_ROUTER"\nOPENAI_API_KEY=SYNTHETIC_OPENAI\n')
            before=dict(os.environ)
            with patch.object(ll,'ROOT',q):self.assertEqual(ll.project_key(env,'openrouter'),'SYNTHETIC_ROUTER');self.assertEqual(ll.project_key(env,'openai'),'SYNTHETIC_OPENAI')
            self.assertEqual(dict(os.environ),before)
    def test_prior_accounting_carries_uncertainty_without_double_count(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d)
            for name,maximum,prior in [('one','.11190325','0'),('two','.12','.11190325')]:
                p=root/('live_'+name);p.mkdir();(p/'result.json').write_text(json.dumps({'evidence':'actual_live_provider','prior_accounted_usd':prior,'budget':{'attempts':1,'maximum_accounted_usd':maximum}}))
            with patch.object(ll,'ROOT',root):amount,attempted=ll.previous_accounting()
            self.assertEqual(amount,ll.Decimal('.12'));self.assertTrue(attempted)
    def response(self,payload):
        return {'id':'resp_fake','model':'gpt-5-mini','status':'completed','usage':{'input_tokens':100,'output_tokens':50},'output':[{'type':'message','content':[{'type':'output_text','text':payload}]}]}
    def test_optimizer_and_runtime_share_same_budget(self):
        budget=ll.Budget();session=ll.SharedProvider('SYNTHETIC_SECRET',budget)
        class FakeTransport:
            stage=''
            def __call__(self,request,timeout=40):
                budget.reserve();result=self_result;budget.finish(result,self.stage,1);return io.BytesIO(json.dumps(result).encode())
        self_result=self.response('[[ ## answer ## ]]\nfixture\n[[ ## completed ## ]]')
        fake=FakeTransport();session.transport=fake;session.client.opener=fake
        session.call([{'role':'user','content':'Synthetic optimizer request'}])
        session.runtime_client(stage='response',model='gpt-5-mini',effort='medium',instructions='Synthetic',input='Synthetic',schema={'type':'object'},tools=[])
        self.assertEqual(budget.attempts,2);self.assertEqual([r['stage'] for r in budget.rows],['dspy_optimizer','paired_response'])
    def test_optimizer_context_rejected_before_dispatch(self):
        session=ll.SharedProvider('SYNTHETIC_SECRET',ll.Budget())
        with self.assertRaises(ll.StopQA):session.call([{'content':'x'*100001}])
        self.assertEqual(session.budget.attempts,0)
    def test_noninteractive_refuses_without_key_prompt(self):
        with patch.object(ll,'verify_build'),patch('sys.stdin.isatty',return_value=False),patch('getpass.getpass') as prompt:
            with self.assertRaises(ll.StopQA):ll.run()
            prompt.assert_not_called()
    def test_secret_candidate_refused_before_sqlite(self):
        with tempfile.TemporaryDirectory() as d:
            class Provider:budget=ll.Budget()
            cfg=json.loads((ll.ROOT/'verified_source/config.json').read_text())
            proposal=dict(instructions='SYNTHETIC_SECRET',demos=[],optimizer='fixture',dspy_version='fixture')
            with patch.object(ll,'compile_with_budgeted_optimizer',return_value=proposal):report=ll.execute(pathlib.Path(d),Provider(),cfg,'SYNTHETIC_SECRET')
            self.assertEqual(report['status'],'stopped_incomplete');self.assertFalse(contains_secret(json.loads((pathlib.Path(d)/'result.json').read_text()),'SYNTHETIC_SECRET'))
            import sqlite3
            with sqlite3.connect(pathlib.Path(d)/'synthetic_learning.db') as db:self.assertEqual(db.execute('SELECT count(*) FROM learning_candidates').fetchone()[0],0)
    def test_empty_key_refuses_without_transport_and_clears_lock(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(ll,'ROOT',pathlib.Path(d)),patch.object(ll,'verify_build'),patch('sys.stdin.isatty',return_value=True),patch('getpass.getpass',return_value=''),patch.object(ll,'SharedProvider') as provider:
                with self.assertRaises(ll.StopQA):ll.run()
                provider.assert_not_called();self.assertFalse((pathlib.Path(d)/'live_learning.lock').exists())
    def test_prior_paid_artifact_refuses_before_key_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            folder=pathlib.Path(d)/'live_learning_prior';folder.mkdir();(folder/'result.json').write_text(json.dumps({'evidence':'actual_live_provider_if_executed','budget':{'attempts':1}}))
            with patch.object(ll,'ROOT',pathlib.Path(d)),patch.object(ll,'verify_build'),patch('sys.stdin.isatty',return_value=True),patch('getpass.getpass') as prompt:
                with self.assertRaises(ll.StopQA):ll.run()
                prompt.assert_not_called()

    def test_full_paired_evaluation_with_fake_provider_no_promotion(self):
        from tests.test_runtime import envelope,intake
        class Provider:
            def __init__(self):self.budget=ll.Budget()
            def runtime_client(self,**kw):
                self.budget.reserve()
                if kw['stage']=='intake':result=envelope(intake())
                else:
                    text=json.loads(kw['input'][0]['content'])['message']
                    answer='63' if '62 to 63' in text else 'resolved' if 'status label' in text else 'not confirmed' if 'pollen' in text else 'unknown'
                    result=envelope({'message':answer,'source_ids':[],'citations':[]})
                result.update(id='resp_fake_'+str(self.budget.attempts),model='gpt-5-mini',usage={'input_tokens':100,'output_tokens':50})
                self.budget.finish(result,'fixture_'+kw['stage'],1);return result
        with tempfile.TemporaryDirectory() as d:
            cfg=json.loads((ll.ROOT/'verified_source/config.json').read_text());provider=Provider()
            def compile_fixture(train,dev,budget):return dict(instructions='Fixture style only.',demos=[{'input':train[0]['input'],'expected':train[0]['expected']}],optimizer='DSPy injected budgeted optimizer',dspy_version='fixture')
            with patch.object(ll,'compile_with_budgeted_optimizer',side_effect=compile_fixture):report=ll.execute(pathlib.Path(d),provider,cfg,'SYNTHETIC_SECRET')
            self.assertEqual(report['status'],'complete',report);self.assertEqual(report['evaluation']['baseline'],6);self.assertEqual(report['evaluation']['candidate'],6);self.assertFalse(report['evaluation']['eligible']);self.assertIsNone(report['active_candidate']);self.assertEqual(provider.budget.attempts,16)

    def test_adapter_compilation_routes_through_guarded_lm(self):
        import dspy
        from dspy_learning import compile_with_budgeted_optimizer,ProviderBudget
        seen=[]
        def fake_provider(**kw):seen.append(kw);return {'model':'gpt-5-mini','text':'fixture','usage':{'input_tokens':10,'output_tokens':10}}
        budget=ProviderBudget(fake_provider,authorized=True)
        class FixtureOptimizer:
            def __init__(self,**kw):self.lm=kw['task_model']
            def compile(self,program,**kw):
                self.lm(messages=[{'role':'user','content':'Synthetic proposal'}]);program.demos=kw['trainset'][:1];return program
        with patch.object(dspy,'MIPROv2',FixtureOptimizer):proposal=compile_with_budgeted_optimizer([{'input':'synthetic','expected':'reported'}],[{'input':'dev','expected':'unknown'}],budget=budget)
        self.assertEqual(len(seen),1);self.assertEqual(proposal['demos'],[{'input':'synthetic','expected':'reported'}]);self.assertEqual(budget.calls[0]['status'],'complete')

    def test_two_model_benchmark_fixture_counts_and_no_activation(self):
        from model_benchmark import benchmark
        from tests.test_runtime import intake,envelope
        class Shared:
            rows=[];stopped=False
        shared=Shared();shared.rows=[]
        class Session:
            def __init__(self,model):self.model_id=model;self.route={'model':model,'provider':'fixture','metadata_sha256':'fixture'};self.budget=shared
            def runtime_client(self,**kw):
                if kw['stage']=='intake':
                    text=json.loads(kw['input'])['message'];mid='M1' if 'Mira' in text else 'M2' if 'Dev' in text else None
                    result=envelope(intake(member_id=mid,topic='cough' if 'cough' in text else 'other'))
                else:
                    text=json.loads(kw['input'][0]['content'])['message'];answer='53' if 'old is' in text else 'latex' if 'allergy' in text else 'unknown' if 'temperature' in text else 'resolved'
                    result=envelope({'message':answer,'source_ids':[],'citations':[]})
                id='fixture_'+str(len(shared.rows));shared.rows.append(dict(response_id=id,model=self.model_id,provider='fixture',stage='fixture',input_tokens=100,output_tokens=20,reasoning_tokens=0,cached_tokens=0,reported_cost_usd='0.0001',conservative_accounted_usd='0.0001'))
                result.update(id=id,model=self.model_id,usage={'input_tokens':100,'output_tokens':20});return result
        with tempfile.TemporaryDirectory() as d:
            cfg=json.loads((ll.ROOT/'verified_source/config.json').read_text());report=benchmark(pathlib.Path(d),[Session('test/a'),Session('test/b')],cfg)
            self.assertEqual(report['status'],'complete_pilot');self.assertEqual([m['quality_n'] for m in report['models']],[18,18]);self.assertTrue(all(m['quality_passes']==18 for m in report['models']));self.assertEqual(report['activation'],'none');self.assertIsNone(report['winner']);self.assertIsNone(report['ttft_ms']);self.assertTrue(all(m['latency_general']['n']==12 for m in report['models']))
            # Missing stage evidence cannot become an attributed model failure or winner.
            with patch('model_benchmark.judge',return_value=(False,False)):
                failed=benchmark(pathlib.Path(d),[Session('test/a'),Session('test/b')],cfg)
            self.assertEqual(failed['failure_review_gate'],'pending');self.assertNotIn('pilot_candidates',failed)
            self.assertEqual(len(failed['failure_reviews']),24)
            self.assertTrue(all(r['attribution']=='inconclusive' and r['stage_trace_file'] is None for r in failed['failure_reviews']))
            self.assertEqual(failed['model_only_score']['denominator'],0)
