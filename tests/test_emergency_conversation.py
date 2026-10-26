"""Synthetic emergency hold versus unrelated administration; no provider calls."""
import json,tempfile,unittest,http.client,threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from runtime import Runtime
from server import handler_for
from tests.test_runtime import ScriptedClient,envelope,intake

class EmergencyConversationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'test.db';self.cfg=json.loads(Path('config.json').read_text())
 def test_registration_and_explanation_preserve_hold_without_model(self):
  runtime=Runtime(self.path,None,config=self.cfg);self.addCleanup(runtime.close)
  self.assertEqual(runtime.chat('qa','A fictional person cannot breathe.')['route'],'emergency')
  registration=runtime.chat('qa',"I'd like to add a family member.")
  self.assertEqual(registration['route'],'register');self.assertIn('relationship',registration['message']);self.assertIn(self.cfg['messages']['emergency'],registration['context_note']);self.assertEqual(registration['trace']['calls'],[])
  for text in ('whaat can be ?','what?','what do you mean?','why this warning?'):
   with self.subTest(text=text):
    answer=runtime.chat('qa',text);self.assertEqual(answer['route'],'emergency');self.assertEqual(answer['message'],self.cfg['messages']['emergency']);self.assertIn('earlier report',answer['context_note']);self.assertEqual(answer['trace']['calls'],[])
  self.assertTrue(runtime.state('qa')['unassigned_emergency']);self.assertEqual(runtime.state('qa')['members'],{})
 def test_registration_cannot_mask_new_danger(self):
  runtime=Runtime(self.path,None,config=self.cfg);self.addCleanup(runtime.close)
  runtime.chat('qa','Someone cannot breathe.')
  result=runtime.chat('qa',"I'd like to add a family member but she cannot breathe.")
  self.assertEqual(result['route'],'emergency');self.assertEqual(result['message'],self.cfg['messages']['emergency'])
 def test_unbound_review_says_no_notification_was_queued(self):
  self.cfg['human_review']={'enabled':True}
  client=ScriptedClient(envelope(intake(intent='symptom',topic='cough',review='doctor',review_evidence='moderate',observations=[{'key':'severity','value':'moderate','evidence':'moderate'}])))
  runtime=Runtime(self.path,client,config=self.cfg);self.addCleanup(runtime.close)
  result=runtime.chat('qa','A fictional person has a moderate cough.')
  self.assertEqual(result['route'],'doctor');self.assertEqual(result['message'],self.cfg['messages']['doctor']);self.assertEqual(result['human_review']['status'],'not_queued');self.assertIn('No email was queued',result['context_note'])
 def test_notice_survives_http_history_reload_and_household_scope(self):
  cfg=self.cfg
  class Settings:
   def metadata(self,s):return {'configured':False}
   def engine(self,s,p):return Runtime(p,None,config=cfg)
  server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.path,config=cfg,settings=Settings()));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  def req(method,path,message=None,cookie='a'*32):
   c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5);headers={'Content-Type':'application/json','Cookie':'family_session='+cookie};c.request(method,path,json.dumps({'message':message}) if message else None,headers);r=c.getresponse();b=json.loads(r.read());c.close();self.assertEqual(r.status,200);return b
  try:
   req('POST','/api/chat','Someone cannot breathe.');registration=req('POST','/api/chat',"I'd like to add a family member.");self.assertEqual(registration['route'],'register')
   explanation=req('POST','/api/chat','what?');self.assertIn('earlier report',explanation['context_note'])
   history=req('GET','/api/state');self.assertEqual(history['messages'][-1]['context_note'],explanation['context_note'])
   self.assertFalse(any('context_note' in m for m in req('GET','/api/state',cookie='b'*32)['messages']))
  finally:server.shutdown();server.server_close();thread.join(timeout=3)
