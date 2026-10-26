import unittest,tempfile,json
from pathlib import Path
from dspy_learning import fact_metric
from tests.test_runtime import ScriptedClient,envelope,intake
from health_agent import CONFIG_PATH
from runtime import Runtime,intake_schema

class OptimizerOracleTests(unittest.TestCase):
    def test_natural_numeric_and_unknown_dev_answers_are_not_false_negatives(self):
        for expected,text in [('56','The current age is 56.'),('unknown','There is no available data about the temperature.'),('unknown',"I don't have information to provide."),('unknown','The temperature has not been measured.'),('unknown',"I can't report a precise temperature."),('not confirmed','This remains unconfirmed.'),('not confirmed','The cause has not been established.'),('right','The reported side is right.')]:
            with self.subTest(text=text):self.assertTrue(fact_metric(expected,text))

    def test_contradictions_remain_failures(self):
        for expected,text in [('56','The current age is 55.'),('56','It is not 56.'),('56','55 or 56.'),('unknown','It is not unknown.'),('not confirmed','It is confirmed.'),('not confirmed','Previously not confirmed, now confirmed.'),('right','It is not right.')]:
            with self.subTest(text=text):self.assertFalse(fact_metric(expected,text))

    def test_ordinary_hypothesis_query_cannot_be_misrouted_as_unrequested_capability(self):
        text='Explain whether a possible cause is established in a fictional scenario.'
        c=ScriptedClient(envelope(intake(intent='unsupported')),envelope({'message':'A possible cause is not established by that statement.','source_ids':[],'citations':[]}))
        cfg=json.loads(CONFIG_PATH.read_text());cfg['members']={}
        self.assertNotIn('unsupported',intake_schema(cfg['topics'],source=text)['properties']['intent']['enum'])
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'db',c,config=cfg)
            try:
                answer=r.chat('synthetic',text);self.assertEqual(answer['route'],'general');self.assertIn('unsupported_route_not_grounded_in_request',answer['trace']['guardrails'])
            finally:r.close()

    def test_lab_analysis_stays_out_of_scope(self):
        text='Analyze this lab report.';cfg=json.loads(CONFIG_PATH.read_text());cfg['members']={}
        c=ScriptedClient(envelope(intake(intent='unsupported')))
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'db',c,config=cfg)
            try:self.assertEqual(r.chat('synthetic',text)['route'],'unsupported')
            finally:r.close()

    def test_unavailable_measurement_cannot_create_numeric_fact_or_patient(self):
        text='No thermometer reading is available. What temperature can you report?'
        c=ScriptedClient(envelope(intake(intent='symptom',topic='fever',observations=[{'key':'temperature_c','value':42,'evidence':text}],unavailable_fields=['temperature_c'])),envelope({'message':"I cannot report an exact temperature without a reading.",'source_ids':[],'citations':[]}))
        cfg=json.loads(CONFIG_PATH.read_text());cfg['members']={}
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'db',c,config=cfg)
            try:
                answer=r.chat('synthetic',text);self.assertEqual(answer['route'],'general');self.assertEqual(r.state('synthetic')['episodes'],{});self.assertIn('unknown_measurement_not_numeric',answer['trace']['guardrails'])
                self.assertTrue(fact_metric('unknown',answer['message']))
            finally:r.close()

    def test_hypothetical_cause_question_does_not_become_symptom_by_keyword(self):
        text='Fictional example: one possible trigger for a cough was suggested. Has the cause been established?'
        c=ScriptedClient(envelope(intake()),envelope({'message':'No cause is established by a suggestion alone.','source_ids':[],'citations':[]}))
        cfg=json.loads(CONFIG_PATH.read_text());cfg['members']={}
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'db',c,config=cfg)
            try:
                a=r.chat('synthetic',text);self.assertEqual(a['route'],'general');self.assertEqual(r.state('synthetic')['episodes'],{})
            finally:r.close()

    def test_age_only_nonclinical_example_is_not_a_family_registration(self):
        from tests.test_runtime import profile
        text='For a fictional example, the age was corrected from 72 to 73. State the corrected age briefly.'
        c=ScriptedClient(envelope(intake(intent='register',profile_updates=[profile(age=876,age_evidence='73')])),envelope({'message':'The corrected age is 73.','source_ids':[],'citations':[]}))
        cfg=json.loads(CONFIG_PATH.read_text());cfg['members']={}
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'db',c,config=cfg)
            try:
                a=r.chat('synthetic',text);self.assertEqual(a['route'],'general');self.assertEqual(r.state('synthetic')['members'],{});self.assertIn('nonclinical_example_read_only',a['trace']['guardrails'])
            finally:r.close()
