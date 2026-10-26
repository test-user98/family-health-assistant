"""Recall questions may read saved facts without fabricating fresh evidence."""
import json
import unittest

from runtime import Runtime
from tests.test_runtime import ScriptedClient, envelope, intake, profile


class ModelGroundingTests(unittest.TestCase):
    def test_repeated_old_memory_is_not_rejected_or_saved_as_a_new_report(self):
        def recall(request):
            state = json.loads(request['input'])['state']
            return envelope(intake(member_id=state['active_member_id'],
                memories=[{'key': 'allergies', 'value': 'latex', 'evidence': 'latex'}]))
        client = ScriptedClient(
            envelope(intake(intent='register', profile_updates=[profile(name='Raj', age=552,
                name_evidence='Raj', age_evidence='46')], memories=[
                {'key': 'allergies', 'value': 'latex', 'evidence': 'latex'}])),
            envelope({'message': 'Saved Raj and his reported allergy.', 'source_ids': []}),
            recall, envelope({'message': 'You reported a latex allergy for Raj.', 'source_ids': []}))
        runtime = Runtime(':memory:', client)
        self.addCleanup(runtime.close)
        runtime.chat('family', 'My father Raj is 46 years old and is allergic to latex')
        answer = runtime.chat('family', 'What allergy have I told you about Raj?')
        self.assertEqual(answer['route'], 'general')
        self.assertIn('latex', answer['message'])
        self.assertIn('ungrounded_memory_ignored', answer['trace']['guardrails'])
        self.assertEqual(next(iter(runtime.state('family')['members'].values()))['facts']['allergies'], ['latex'])

    def test_hallucinated_memory_cannot_be_written_during_a_recall_question(self):
        client = ScriptedClient(
            envelope(intake(intent='register', profile_updates=[profile(name='Raj', age=552,
                name_evidence='Raj', age_evidence='46')])),
            envelope({'message': 'Saved Raj.', 'source_ids': []}),
            lambda request: envelope(intake(member_id=json.loads(request['input'])['state']['active_member_id'],
                memories=[{'key': 'conditions', 'value': 'diabetes', 'evidence': 'diabetes'}])),
            envelope({'message': 'No condition has been recorded for Raj.', 'source_ids': []}))
        runtime = Runtime(':memory:', client)
        self.addCleanup(runtime.close)
        runtime.chat('family', 'My father Raj is 46 years old')
        answer = runtime.chat('family', 'What conditions have I reported for Raj?')
        self.assertEqual(answer['route'], 'general')
        self.assertEqual(next(iter(runtime.state('family')['members'].values()))['facts'], {})

    def test_provider_schema_limits_ids_to_this_households_real_records(self):
        client = ScriptedClient(envelope(intake()), envelope({'message': 'How can I help?', 'source_ids': []}))
        runtime = Runtime(':memory:', client)
        self.addCleanup(runtime.close)
        runtime.chat('empty-family', 'Can you help with a general question?')
        schema = client.calls[0]['schema']
        self.assertEqual(schema['properties']['member_id']['enum'], [None])
        self.assertEqual(schema['properties']['episode_id']['enum'], [None])
        self.assertEqual(schema['properties']['profile_updates']['items']['properties']['member_id']['enum'], [None])
