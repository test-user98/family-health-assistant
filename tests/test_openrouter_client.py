"""Official-protocol shapes exercised with fake transport; zero OpenRouter inference."""
import copy,io,json,tempfile,unittest
from pathlib import Path
from openrouter_client import route,RouterError,RouterBudget,OpenRouterSession,messages_from_inputs,ENDPOINT
from runtime import Runtime,SEARCH_TOOL
from health_agent import CONFIG_PATH
from tests.test_runtime import intake

def metadata():return {'data':{'id':'test/model','endpoints':[{'tag':'test-route','provider_name':'TestProvider','name':'TestProvider | version-1','context_length':10000,'max_completion_tokens':5000,'pricing':{'prompt':'0.00000025','completion':'0.000002'},'supported_parameters':['response_format','structured_outputs','tools','tool_choice','max_tokens']}]}}
def fixture(text='{}',**changes):
    r=dict(id='generation-fixture',provider='TestProvider',model='test/model',choices=[{'finish_reason':'stop','message':{'content':text}}],usage={'prompt_tokens':100,'completion_tokens':20,'cost':0.000065,'completion_tokens_details':{'reasoning_tokens':0},'prompt_tokens_details':{'cached_tokens':0}});r.update(changes);return r
class Transport:
    def __init__(self,*responses):self.responses=list(responses);self.requests=[]
    def open(self,request,timeout):self.requests.append(request);return io.BytesIO(json.dumps(self.responses.pop(0)).encode())
class OpenRouterTests(unittest.TestCase):
    def setUp(self):self.r=route('test/model','test-route',lambda url:metadata())
    def test_exact_metadata_capabilities_and_current_route_prices(self):
        self.assertEqual(self.r['model'],'test/model');self.assertEqual(self.r['rates']['prompt'],'2.5E-7');self.assertIn('metadata_sha256',self.r)
        d=metadata();d['data']['endpoints'][0]['supported_parameters'].remove('structured_outputs')
        with self.assertRaises(RouterError):route('test/model','test-route',lambda u:d)
        with self.assertRaises(RouterError):route('test/model:online','test-route',lambda u:metadata())
        with self.assertRaises(RouterError):route('openrouter/auto','test-route',lambda u:metadata())
        with self.assertRaises(RouterError):route('test/model','other',lambda u:metadata())
    def test_invalid_or_expensive_price_refuses_before_request(self):
        d=metadata();d['data']['endpoints'][0]['pricing']['prompt']='NaN'
        with self.assertRaises(RouterError):route('test/model','test-route',lambda u:d)
        r=copy.deepcopy(self.r);r['rates']['prompt']='1';budget=RouterBudget()
        with self.assertRaises(RouterError):budget.reserve(r)
        self.assertEqual(budget.attempts,0)
    def test_no_fallbacks_schema_tools_and_endpoint_pin(self):
        t=Transport(fixture());s=OpenRouterSession('SYNTHETIC_SECRET',self.r,transport=t)
        s.complete([{'role':'user','content':'Synthetic'}],stage='test',schema={'type':'object'},tools=[SEARCH_TOOL],tool_choice='required')
        request=t.requests[0];p=json.loads(request.data);self.assertEqual(request.full_url,ENDPOINT);self.assertFalse(p['provider']['allow_fallbacks']);self.assertTrue(p['provider']['require_parameters']);self.assertEqual(p['provider']['only'],['test-route']);self.assertEqual(p['tools'][0]['function']['name'],'web_search');self.assertTrue(p['response_format']['json_schema']['strict']);self.assertNotIn('plugins',p)
    def test_native_cost_usage_and_missing_cost_stop(self):
        b=RouterBudget();t=Transport(fixture(),fixture(usage={'prompt_tokens':1,'completion_tokens':1}));s=OpenRouterSession('SYNTHETIC_SECRET',self.r,b,t)
        from decimal import Decimal
        s.call([{'role':'user','content':'Synthetic'}]);self.assertEqual(b.spent,Decimal('0.000065'))
        with self.assertRaises(RouterError):s.call([])
        self.assertTrue(b.stopped);self.assertGreater(b.reserved,0)
        with self.assertRaises(RouterError):s.call([])
        self.assertEqual(len(t.requests),2)
    def test_prior_reserve_is_charged_before_any_new_request(self):
        from decimal import Decimal
        b=RouterBudget('.11190325');self.assertEqual(b.spent,Decimal('.11190325'));self.assertEqual(b.summary()['carried_in_usd'],'0.11190325')
        b=RouterBudget('.89')
        with self.assertRaises(RouterError):b.reserve(self.r)
        self.assertEqual(b.attempts,0)
    def test_http_diagnostics_do_not_copy_provider_message(self):
        import urllib.error
        class Failing:
            def open(self,*a,**kw):raise urllib.error.HTTPError(ENDPOINT,400,'bad',{},io.BytesIO(json.dumps({'error':{'code':400,'message':'SYNTHETIC_SECRET and PRIVATE_CANARY'}}).encode()))
        s=OpenRouterSession('SYNTHETIC_SECRET',self.r,transport=Failing())
        with self.assertRaises(RouterError):s.call([])
        diagnostics=json.dumps(s.budget.summary());self.assertNotIn('SYNTHETIC_SECRET',diagnostics);self.assertNotIn('PRIVATE_CANARY',diagnostics);self.assertEqual(s.budget.failures[0]['http_status'],400)
    def test_wrong_model_or_provider_and_overcharge_stop(self):
        for change in [dict(model='other/model'),dict(provider='OtherProvider'),dict(usage={'prompt_tokens':1,'completion_tokens':1,'cost':99})]:
            with self.subTest(change=change):
                s=OpenRouterSession('SYNTHETIC_SECRET',self.r,transport=Transport(fixture(**change)))
                with self.assertRaises(RouterError):s.call([])
                self.assertTrue(s.budget.stopped)
    def test_sensitive_unicode_echo_refused_before_runtime(self):
        t=Transport(fixture(json.dumps({'nested':'SYNTHETIC_SECRET'})));s=OpenRouterSession('SYNTHETIC_SECRET',self.r,transport=t)
        with self.assertRaises(RouterError):s.call([])
        # Safe usage receipts persist even when unsafe response content is rejected.
        self.assertEqual(len(s.budget.rows),1);self.assertNotIn('SYNTHETIC_SECRET',json.dumps(s.budget.summary()))

    def test_durable_reservation_precedes_transport_and_receipt_precedes_diagnostic_failure(self):
        snapshots=[];b=RouterBudget('.90',cap='4.80',authorization='5.00',persist=lambda s:snapshots.append(copy.deepcopy(s)))
        class DeepTransport(Transport):
            def open(self,*args,**kwargs):
                self_outer.assertEqual(snapshots[-1]['reservations'][-1]['status'],'reserved_before_dispatch')
                return super().open(*args,**kwargs)
        self_outer=self;deep='value'
        for _ in range(15):deep={'nested':deep}
        s=OpenRouterSession('SYNTHETIC_SECRET',self.r,b,DeepTransport(fixture(extra=deep)))
        with self.assertRaises(RouterError):s.call([])
        self.assertEqual(len(snapshots[-1]['calls']),1);self.assertEqual(snapshots[-1]['uncertain_reserve_usd'],'0')
        self.assertTrue(snapshots[-1]['stopped']);self.assertEqual(snapshots[-1]['carried_in_usd'],'0.90')
        with self.assertRaises(RouterError):s.call([])

    def test_disk_failure_before_reservation_prevents_paid_dispatch(self):
        def fail(summary):raise OSError('synthetic disk full')
        t=Transport(fixture());s=OpenRouterSession('SYNTHETIC_SECRET',self.r,RouterBudget(persist=fail),t)
        with self.assertRaises(RouterError):s.call([])
        self.assertEqual(t.requests,[])

    def test_nested_provider_schema_error_is_classified_without_body_or_secret(self):
        import urllib.error
        raw=json.dumps({'error':{'type':'invalid_request_error','message':'Invalid schema: enum values must be unique SYNTHETIC_SECRET PRIVATE_CANARY'}})
        class Failing:
            def open(self,*a,**kw):raise urllib.error.HTTPError(ENDPOINT,400,'bad',{},io.BytesIO(json.dumps({'error':{'code':400,'message':'Provider returned error','metadata':{'raw':raw}}}).encode()))
        s=OpenRouterSession('SYNTHETIC_SECRET',self.r,transport=Failing())
        with self.assertRaises(RouterError):s.call([])
        summary=json.dumps(s.budget.summary());self.assertNotIn('SYNTHETIC_SECRET',summary);self.assertNotIn('PRIVATE_CANARY',summary)
        self.assertEqual(s.budget.failures[0]['category'],'schema_duplicate_enum');self.assertIn('provider_error_sha256',s.budget.failures[0])
    def test_function_observation_feedback_conversion(self):
        rows=messages_from_inputs([{'role':'user','content':'Query'},{'type':'function_call','call_id':'one','name':'web_search','arguments':'{}'},{'type':'function_call','call_id':'two','name':'web_search','arguments':'{}'},{'type':'function_call_output','call_id':'one','output':'{}'},{'type':'function_call_output','call_id':'two','output':'{}'}])
        self.assertEqual(len(rows[1]['tool_calls']),2);self.assertEqual(rows[2]['tool_call_id'],'one');self.assertEqual(rows[3]['role'],'tool')
    def test_actual_runtime_uses_chat_adapter_and_returns_envelope(self):
        t=Transport(fixture(json.dumps(intake())),fixture(json.dumps({'message':'Synthetic answer','source_ids':[],'citations':[]})))
        s=OpenRouterSession('SYNTHETIC_SECRET',self.r,transport=t);cfg=json.loads(CONFIG_PATH.read_text());cfg['models']={k:{'model':'test/model','effort':'none'} for k in ('conversation','assessment')}
        with tempfile.TemporaryDirectory() as d:
            runtime=Runtime(Path(d)/'synthetic.db',s.runtime_client,config=cfg)
            try:
                out=runtime.chat('A','Explain general wellbeing.');self.assertEqual(out['message'],'Synthetic answer');self.assertEqual(out['route'],'general');self.assertEqual(len(s.budget.rows),2);self.assertEqual(out['trace']['calls'][0]['response_id'],'generation-fixture')
            finally:runtime.close()
