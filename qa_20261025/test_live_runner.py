import copy,io,json,pathlib,tempfile,unittest,urllib.request
from unittest.mock import patch
from decimal import Decimal
from live_runner import Budget,StopQA,WORST,CAP,SafeTransport,checkpoint

def response(i=1000,o=100):return {'id':'resp_synthetic','model':'gpt-5-mini-2025-08-07','status':'completed','usage':{'input_tokens':i,'output_tokens':o,'input_tokens_details':{'cached_tokens':0},'output_tokens_details':{'reasoning_tokens':10}},'output':[],'service_tier':'default'}
def request(**changes):
 p={'model':'gpt-5-mini','store':False,'max_output_tokens':5000,'input':'Fictional query'};p.update(changes)
 return urllib.request.Request('https://api.openai.com/v1/responses',json.dumps(p).encode())
class FakeOpener:
 def __init__(self,data):self.data=data;self.calls=0
 def open(self,req,timeout):self.calls+=1;return io.BytesIO(json.dumps(self.data).encode())
class Tests(unittest.TestCase):
 def test_worst_case_math(self): self.assertEqual(WORST,Decimal('.11'));self.assertEqual(CAP,Decimal('.90'))
 def test_ninth_full_request_blocked_before_dispatch(self):
  b=Budget()
  for _ in range(8):b.reserve();b.finish(response(400000,5000),'intake',1)
  with self.assertRaises(StopQA):b.reserve()
  self.assertEqual(b.attempts,8);self.assertEqual(b.spent,Decimal('.88'))
 def test_actual_usage_allows_bounded_twenty_turn_run(self):
  b=Budget()
  for _ in range(52):b.reserve();b.finish(response(10000,1000),'intake',1)
  self.assertEqual(b.attempts,52);self.assertEqual(b.spent,Decimal('.234'))
 def test_sixty_call_cap(self):
  b=Budget()
  for _ in range(60):b.reserve();b.finish(response(),'intake',1)
  with self.assertRaises(StopQA):b.reserve()
 def test_inflight_and_uncertain_outcome_stop(self):
  b=Budget();b.reserve()
  with self.assertRaises(StopQA):b.reserve()
  b.fail()
  with self.assertRaises(StopQA):b.reserve()
  self.assertEqual(b.reserved,Decimal('.11'))
 def test_missing_usage_refused(self):
  b=Budget();b.reserve()
  with self.assertRaises(StopQA):b.finish({},'intake',0)
  self.assertTrue(b.stopped);self.assertEqual(b.reserved,Decimal('.11'))
 def test_usage_over_limit_or_other_model_refused(self):
  for data in [response(400001,1),response(1,5001),dict(response(),model='other')]:
   b=Budget();b.reserve()
   with self.assertRaises(StopQA):b.finish(data,'intake',0)
 def test_redaction_before_disk(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(StopQA):checkpoint(pathlib.Path(d),{'value':'SYNTHETIC_SECRET'},Budget(),'SYNTHETIC_SECRET')
   self.assertEqual(list(pathlib.Path(d).iterdir()),[])
 def test_transport_blocks_other_destination_and_paid_tool(self):
  b=Budget();t=SafeTransport(b,'SYNTHETIC_SECRET');t.stage='intake'
  for req in [urllib.request.Request('https://example.com',b'{}'),request(tools=[{'type':'web_search'}]),request(model='other'),request(service_tier='priority')]:
   with self.assertRaises(StopQA):t(req)
  self.assertEqual(b.attempts,0)
 def test_transport_pins_tier_and_uses_usage(self):
  b=Budget();t=SafeTransport(b,'SYNTHETIC_SECRET');t.stage='intake';fake=FakeOpener(response());req=request()
  with patch('urllib.request.build_opener',return_value=fake):t(req)
  self.assertEqual(json.loads(req.data)['service_tier'],'default');self.assertEqual(b.attempts,1);self.assertEqual(b.reserved,0)
 def test_provider_echo_secret_refused_before_runtime(self):
  b=Budget();t=SafeTransport(b,'SYNTHETIC_SECRET');t.stage='intake';fake=FakeOpener(dict(response(),output='SYNTHETIC_SECRET'))
  with patch('urllib.request.build_opener',return_value=fake):
   with self.assertRaises(StopQA) as caught:t(request())
  self.assertNotIn('SYNTHETIC_SECRET',str(caught.exception));self.assertTrue(b.stopped)
 def test_no_retry_on_transport_error(self):
  b=Budget();t=SafeTransport(b,'SYNTHETIC_SECRET');t.stage='intake'
  with patch('urllib.request.build_opener',side_effect=RuntimeError('SYNTHETIC_SECRET')) as fake:
   with self.assertRaises(StopQA) as caught:t(request())
  self.assertEqual(fake.call_count,1);self.assertNotIn('SYNTHETIC_SECRET',str(caught.exception));self.assertEqual(b.reserved,Decimal('.11'))
 def test_provider_refusal_records_error_without_reassurance(self):
  from live_runner import Client,Runtime
  with tempfile.TemporaryDirectory() as d:
   b=Budget();client=Client('SYNTHETIC_SECRET',b)
   data=dict(response(),output=[{'type':'message','content':[{'type':'refusal','refusal':'Cannot answer.'}]}])
   # This transport intentionally tests native OpenAI gpt-5-mini, independently
   # of the app's measured OpenRouter default. Otherwise the client rejects the
   # mismatched model before the fake refusal response is ever exercised.
   cfg=json.loads((pathlib.Path(__file__).resolve().parents[1]/'config.json').read_text())
   cfg['models']={stage:{'model':'gpt-5-mini','effort':'low'} for stage in ('conversation','assessment')}
   fake=FakeOpener(data);r=Runtime(pathlib.Path(d)/'synthetic.db',client,config=cfg)
   try:
    with patch('urllib.request.build_opener',return_value=fake):result=r.chat('synthetic','Explain hydration briefly.')
    self.assertEqual(result['route'],'error');self.assertEqual(fake.calls,1);self.assertEqual(b.attempts,1)
   finally:r.close()
 def test_unicode_escaped_provider_echo_is_refused_before_runtime(self):
  class Escaped:
   def open(self,req,timeout):
    data=dict(response(),output=[{'type':'message','content':[{'type':'output_text','text':'{"message":"SYNTHETIC_SECRET","source_ids":[]}'}]}])
    return io.BytesIO(json.dumps(data).replace('SYNTHETIC_SECRET','SYNTHETIC_\\u0053ECRET').encode())
  b=Budget();t=SafeTransport(b,'SYNTHETIC_SECRET');t.stage='response'
  with patch('urllib.request.build_opener',return_value=Escaped()):
   with self.assertRaises(StopQA):t(request())
 def test_keyboard_interrupt_retains_reservation(self):
  class Interrupted:
   def open(self,req,timeout):raise KeyboardInterrupt()
  b=Budget();t=SafeTransport(b,'SYNTHETIC_SECRET');t.stage='intake'
  with patch('urllib.request.build_opener',return_value=Interrupted()):
   with self.assertRaises(StopQA):t(request())
  self.assertEqual(b.reserved,Decimal('.11'));self.assertTrue(b.stopped)
 def test_redirect_refused(self):
  from live_runner import NoRedirect
  with self.assertRaises(StopQA):NoRedirect().redirect_request(request(),None,302,'',{},'https://example.com')
 def test_exact_cap_boundary(self):
  b=Budget();b.spent=Decimal('.79');b.reserve();self.assertEqual(b.spent+b.reserved,Decimal('.90'))
  b=Budget();b.spent=Decimal('.7900001')
  with self.assertRaises(StopQA):b.reserve()
 def test_cached_usage_does_not_reduce_conservative_charge(self):
  b=Budget();b.reserve();data=response(1000,100);data['usage']['input_tokens_details']['cached_tokens']=1000;b.finish(data,'intake',0)
  self.assertEqual(b.spent,Decimal('.00045'));self.assertEqual(b.rows[0]['cached_tokens'],1000)
 def test_runner_full_24_turn_flow_with_fake_provider_and_fresh_process(self):
  import contextlib,os
  import live_runner as lr
  from tests.test_runtime import envelope,intake
  class DynamicFake:
   def __init__(self):self.calls=0
   def open(self,req,timeout):
    self.calls+=1;p=json.loads(req.data);stage=p['text']['format']['name'];data=response();data.update(envelope(intake() if stage=='intake' else {'message':'Fictional test reply.','source_ids':[]}));return io.BytesIO(json.dumps(data).encode())
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);(root/'verified_source').symlink_to(lr.SOURCE,target_is_directory=True);sentinel=root/'existing_household.db';sentinel.write_bytes(b'UNCHANGED_EXISTING_SENTINEL');fake=DynamicFake();output=io.StringIO()
   with patch.object(lr,'ROOT',root),patch('sys.stdin.isatty',return_value=True),patch('getpass.getpass',return_value='SYNTHETIC_SECRET'),patch('urllib.request.build_opener',return_value=fake),contextlib.redirect_stdout(output):lr.run()
   folders=list(root.glob('live_*'));self.assertEqual(len(folders),1);record=json.loads((folders[0]/'result.json').read_text());self.assertEqual(record['status'],'completed');self.assertEqual(len(record['turns']),24);self.assertIn('fresh_process_state',record);self.assertEqual(record['other_household_state']['members'],{});self.assertEqual(sentinel.read_bytes(),b'UNCHANGED_EXISTING_SENTINEL');self.assertNotIn('SYNTHETIC_SECRET',output.getvalue());self.assertNotIn('SYNTHETIC_SECRET',(folders[0]/'result.json').read_text());self.assertLessEqual(Decimal(record['budget']['maximum_accounted_usd']),Decimal('.90'))
 def test_noninteractive_input_refused_without_request(self):
  import live_runner as lr
  with patch('sys.stdin.isatty',return_value=False),patch('getpass.getpass') as prompt:
   with self.assertRaises(StopQA):lr.run()
  prompt.assert_not_called()
if __name__=='__main__':unittest.main()
