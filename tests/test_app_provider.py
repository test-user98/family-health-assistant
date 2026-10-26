import json,unittest
from unittest.mock import patch
from provider_adapter import RouterAppClient
from server import RuntimeSettings,handler_for
from health_agent import CONFIG_PATH
from tests.test_openrouter_client import Transport,fixture,metadata
from tests.test_runtime import intake
from tests import test_server

class AppProviderTests(unittest.TestCase):
    def test_actual_settings_and_http_chat_reuse_pinned_router_client_without_key_persistence(self):
        web=test_server.WebTests('test_serves_ui_and_empty_family_with_cookie');web.setUp();self.addCleanup(web.doCleanups)
        cfg=json.loads(CONFIG_PATH.read_text());cfg['members']={}
        t=Transport(fixture('{"ok":true}'),fixture('{"ok":true}'),fixture(json.dumps(intake())),fixture(json.dumps({'message':'A synthetic general answer.','source_ids':[],'citations':[]})))
        clients=[]
        def factory(key,tag,cap):
            c=RouterAppClient(key,tag,cap,fetch=lambda u:metadata(),transport=t);clients.append(c);return c
        settings=RuntimeSettings(cfg,load_credentials=False,router_factory=factory);web.handler=handler_for(web.path,settings=settings,config=cfg)
        _,_,cookie=web.request();payload={'provider':'openrouter','route_provider':'test-route','conversation_model':'test/model','assessment_model':'test/model','conversation_effort':'none','assessment_effort':'none','openrouter_api_key':'SYNTHETIC_SECRET'}
        response,output,_=web.request('/api/settings',cookie=cookie,raw=json.dumps(payload).encode());self.assertEqual(response.status,200);self.assertEqual(output['runtime']['provider'],'openrouter')
        response,answer,_=web.request('/api/chat','Explain general wellbeing.',cookie);self.assertEqual(response.status,200);self.assertEqual(answer['message'],'A synthetic general answer.')
        self.assertEqual(len(clients),1);self.assertEqual(clients[0].budget.attempts,4)
        response,answer,_=web.request('/api/chat','Someone cannot breathe.',cookie);self.assertEqual(answer['route'],'emergency');self.assertEqual(clients[0].budget.attempts,4)
        _,state,_=web.request(cookie=cookie);self.assertNotIn('SYNTHETIC_SECRET',json.dumps(state));self.assertNotIn(b'SYNTHETIC_SECRET',web.path.read_bytes())
        _,other,_=web.request();self.assertFalse(other['runtime']['configured'])
        self.assertTrue(all(json.loads(r.data)['provider']['only']==['test-route'] for r in t.requests))
        clients[0].clear();self.assertIsNone(clients[0].key)

    def test_nondefault_effort_refused_before_network(self):
        t=Transport();c=RouterAppClient('SYNTHETIC_SECRET',transport=t)
        with self.assertRaisesRegex(ValueError,'effort'):c(stage='response',model='test/model',effort='low')
        self.assertEqual(t.requests,[])

    def test_no_router_key_loaded_from_environment(self):
        with patch.dict('os.environ',{'OPENROUTER_API_KEY':'SYNTHETIC_SECRET'}):
            cfg=json.loads(CONFIG_PATH.read_text());cfg['provider']={'name':'openrouter'}
            s=RuntimeSettings(cfg,load_credentials=False);self.assertFalse(s.metadata('A')['configured'])

    def test_failed_reconfiguration_cannot_reset_connection_budget(self):
        cfg=json.loads(CONFIG_PATH.read_text());created=[]
        class Failed:
            def __call__(self,**kwargs):raise ValueError('Outcome unknown; stopped.')
        def factory(*args):created.append(args);return Failed()
        settings=RuntimeSettings(cfg,load_credentials=False,router_factory=factory)
        payload={'provider':'openrouter','route_provider':'openai','openrouter_api_key':'SYNTHETIC_SECRET','conversation_model':'openai/gpt-4.1-mini','assessment_model':'openai/gpt-4.1-mini','conversation_effort':'none','assessment_effort':'none'}
        for _ in range(2):
            with self.assertRaises(ValueError):settings.configure('A',payload)
        self.assertEqual(len(created),1);self.assertFalse(settings.metadata('A')['configured'])

    def test_future_default_is_measured_route_without_ambient_router_key(self):
        settings=RuntimeSettings(load_credentials=False)
        m=settings.metadata('A');self.assertEqual(m['provider'],'openrouter');self.assertEqual(m['route_provider'],'openai');self.assertEqual(m['models']['conversation'],{'model':'openai/gpt-4.1-mini','effort':'none'});self.assertFalse(m['configured'])
