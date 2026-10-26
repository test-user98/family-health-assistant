import json,tempfile,unittest
from pathlib import Path
from runtime import Runtime
from health_agent import CONFIG_PATH
from memory_queries import target
from tests.test_runtime import ScriptedClient,envelope,intake

class MemoryQueryTests(unittest.TestCase):
    def setUp(self):
        self.cfg=json.loads(CONFIG_PATH.read_text());self.cfg['members']={'M1':dict(name='Mira',relationship='mother',aliases=['mother'],age_months=636,facts={})}
    def test_unknown_measurement_in_readback_is_not_a_new_observation(self):
        c=ScriptedClient(envelope(intake(member_id='M1',intent='select',observations=[dict(key='temperature_c',value='unknown',evidence='known')])),envelope({'message':'Her temperature is unknown; it is not recorded.','source_ids':[],'citations':[]}))
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'test.db',c,config=self.cfg)
            try:
                a=r.chat('A',"Is Mira's temperature known? Say unknown if it is not recorded.");self.assertEqual(a['route'],'general');self.assertIn('unknown',a['message']);self.assertEqual(r.state('A')['episodes'],{});self.assertIn('read_only_memory_query',a['trace']['guardrails'])
            finally:r.close()
    def test_age_from_question_is_not_an_observation_or_profile_write(self):
        c=ScriptedClient(envelope(intake(member_id='M1',observations=[dict(key='age_months',value=636,evidence='Mira')])),envelope({'message':'Mira is 53 years old.','source_ids':[],'citations':[]}))
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'test.db',c,config=self.cfg)
            try:self.assertEqual(r.chat('A','How old is Mira?')['route'],'general');self.assertEqual(r.state('A')['members']['M1']['age_months'],636)
            finally:r.close()
    def test_new_reports_medication_and_unknown_identity_never_match(self):
        state={'members':self.cfg['members']}
        for text in ['How old is Mira? She is 54.','What allergy was reported for Mira? She cannot breathe.','Is Mira temperature known now?','What medications are safe for Mira?','How old is Rohan?']:
            with self.subTest(text=text):self.assertIsNone(target(text,state))
    def test_model_escalation_cannot_be_downgraded(self):
        c=ScriptedClient(envelope(intake(member_id='M1',review='doctor',review_evidence='Mira')))
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'test.db',c,config=self.cfg)
            try:self.assertEqual(r.chat('A','How old is Mira?')['message'],self.cfg['messages']['doctor'])
            finally:r.close()
