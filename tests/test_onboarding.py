"""Independent regressions for natural conversation and an initially empty family."""
import tempfile
import unittest
from pathlib import Path

from backends import DemoBackend
from health_agent import Agent


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'family.db'
        self.calls = []

        def forbidden_backend(context):
            self.calls.append(context)
            raise RuntimeError('A clinical interpreter is unnecessary for this turn')

        self.agent = Agent(self.path, forbidden_backend)
        self.addCleanup(self.agent.close)

    def send(self, message):
        return self.agent.chat('household', message)

    def member(self, name):
        matches = [(mid, member) for mid, member in self.agent.state('household')['members'].items()
                   if member['name'].casefold() == name.casefold()]
        self.assertEqual(len(matches), 1, matches)
        return matches[0]

    def assert_nonclinical(self, result):
        self.assertNotIn(result['route'], ('doctor', 'emergency', 'home', 'error'))
        self.assertNotIn('Any difficulty breathing', result['message'])
        self.assertNotIn('How long have these symptoms', result['message'])

    def test_fresh_household_starts_empty_and_hi_is_a_greeting(self):
        initial = self.agent.state('household')
        self.assertEqual(initial['members'], {})
        self.assertEqual(initial['episodes'], {})
        result = self.send('hi')
        self.assert_nonclinical(result)
        self.assertRegex(result['message'].lower(), r'\b(?:hi|hello|hey)\b')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.agent.state('household')['episodes'], {})

    def test_bare_unregistered_name_after_hello_does_not_create_symptoms(self):
        self.send('hi')
        self.assert_nonclinical(self.send('raj'))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.agent.state('household')['episodes'], {})

    def test_register_self_from_natural_sentence(self):
        result = self.send('My name is Sachit, I am 27 years old')
        self.assert_nonclinical(result)
        mid, member = self.member('Sachit')
        self.assertEqual(member['age_months'], 324)
        self.assertIn('i', member['aliases'])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.agent.state('household')['episodes'], {})

    def test_register_father_without_calling_clinical_backend(self):
        self.assert_nonclinical(self.send('Add my father Raj, age 56'))
        mid, member = self.member('Raj')
        self.assertEqual(member['age_months'], 672)
        self.assertIn('father', member['aliases'])
        self.assertEqual(self.calls, [])

    def test_register_mother_in_alternate_natural_wording(self):
        self.assert_nonclinical(self.send('My mother is Sunita, 51'))
        self.assertEqual(self.member('Sunita')[1]['age_months'], 612)
        self.assertEqual(self.calls, [])

    def test_missing_age_has_focused_followup_then_completes_registration(self):
        result = self.send('Add my father Raj')
        self.assert_nonclinical(result)
        self.assertTrue(any(word in result['message'].lower() for word in ('age', 'old')))
        self.assert_nonclinical(self.send('56 years old'))
        self.assertEqual(self.member('Raj')[1]['age_months'], 672)
        self.assertEqual(self.calls, [])

    def test_infant_age_in_months_is_not_interpreted_as_years(self):
        self.assert_nonclinical(self.send('Add my son Ari, age 2 months'))
        self.assertEqual(self.member('Ari')[1]['age_months'], 2)
        self.assertEqual(self.calls, [])

    def test_known_name_selects_member_and_asks_what_help_is_needed(self):
        self.send('Add my father Raj, age 56')
        mid, _ = self.member('Raj')
        result = self.send('Raj')
        self.assert_nonclinical(result)
        self.assertEqual(result['member_id'], mid)
        self.assertEqual(self.agent.state('household')['active_member_id'], mid)
        self.assertEqual(self.agent.state('household')['episodes'], {})
        self.assertEqual(self.calls, [])

    def test_exact_repeated_registration_does_not_duplicate_member(self):
        self.send('Add my father Raj, age 56')
        self.send('Add my father Raj, age 56')
        self.member('Raj')
        self.assertEqual(len(self.agent.state('household')['members']), 1)
        self.assertEqual(self.calls, [])

    def test_invalid_age_is_not_registered_as_medical_fact(self):
        self.assert_nonclinical(self.send('Add my father Raj, age 560'))
        self.assertFalse(any(m.get('age_months', 0) > 1440
                             for m in self.agent.state('household')['members'].values()))
        self.assertEqual(self.calls, [])

    def test_family_registration_survives_restart_and_stays_in_its_household(self):
        self.send('My name is Sachit, I am 27 years old')
        self.send('Add my father Raj, age 56')
        self.agent.close()
        restarted = Agent(self.path, DemoBackend())
        self.addCleanup(restarted.close)
        self.assertEqual({m['name'] for m in restarted.state('household')['members'].values()}, {'Sachit', 'Raj'})
        self.assertEqual(restarted.state('another-household')['members'], {})

    def test_emergency_in_empty_household_overrides_onboarding(self):
        result = self.send('My father cannot breathe')
        self.assertEqual(result['route'], 'emergency')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.agent.state('household')['members'], {})

    def test_no_is_not_accepted_as_a_symptom_duration_and_gets_clearer_prompt(self):
        self.agent.backend = DemoBackend()
        self.send('Add my daughter Maya, age 8')
        self.assertEqual(self.send('Maya has fever')['route'], 'ask')
        duration_question = self.send('No')
        self.assertEqual(duration_question['route'], 'ask')
        self.assertIn('long', duration_question['message'].lower())
        result = self.send('No')
        self.assertEqual(result['route'], 'ask')
        self.assertNotEqual(result['message'], duration_question['message'])
        self.assertTrue(any(word in result['message'].lower() for word in ('hours', 'days', 'duration')))
        self.assertNotEqual(result['route'], 'home')

    def test_unregistered_speaker_does_not_inherit_the_fathers_episode(self):
        self.agent.backend = DemoBackend()
        self.send('Add my father Raj, age 56')
        self.send('Raj has a cough')
        before = self.agent.state('household')['episodes']
        result = self.send('I have a fever')
        self.assertIn(result['route'], ('clarify_member', 'register'))
        self.assertIsNone(result['member_id'])
        self.assertEqual(self.agent.state('household')['episodes'], before)

    def test_negative_age_is_not_silently_changed_to_positive(self):
        result = self.send('Add my father Raj, age -5 years')
        self.assert_nonclinical(result)
        self.assertEqual(self.agent.state('household')['members'], {})

    def test_reported_temperature_does_not_update_registered_age(self):
        self.agent.backend = DemoBackend()
        self.send('Add my father Raj, age 56')
        result = self.send('My father Raj is 39 C with fever')
        self.assertIn(result['route'], ('ask', 'doctor'))
        self.assertEqual(self.member('Raj')[1]['age_months'], 672)

    def test_multiple_introductions_are_not_silently_merged_into_one_person(self):
        result = self.send('My name is Sachit, I am 27, and my father Raj is 56')
        self.assert_nonclinical(result)
        self.assertIn('one person', result['message'])
        self.assertEqual(self.agent.state('household')['members'], {})

    def test_known_relative_symptom_duration_is_not_reinterpreted_as_profile_age(self):
        self.send('Add my father Raj, age 56')
        mid, _ = self.member('Raj')
        self.agent.backend = DemoBackend()
        result = self.send('My father has fever for 1 week')
        self.assertEqual(result['route'], 'doctor')
        self.assertEqual(result['member_id'], mid)
        self.assertEqual(self.member('Raj')[1]['age_months'], 672)
        self.assertFalse(self.agent.state('household').get('registration'))

    def test_registration_sentence_with_fever_continues_to_infant_triage(self):
        self.agent.backend = DemoBackend()
        result = self.send('My son Ari is 2 months old and has fever')
        self.assertEqual(result['route'], 'doctor')
        mid, member = self.member('Ari')
        self.assertEqual(member['age_months'], 2)
        self.assertEqual(result['member_id'], mid)

    def test_cancelled_registration_does_not_transfer_old_symptoms_to_another_member(self):
        self.assert_nonclinical(self.send('My father has fever'))
        self.assert_nonclinical(self.send('cancel'))
        result = self.send('Add my daughter Maya, age 8')
        self.assert_nonclinical(result)
        self.assertEqual(self.agent.state('household')['episodes'], {})
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.agent.state('household').get('pending_message'))

    def test_i_am_having_a_fever_is_not_a_new_person_named_having(self):
        self.send('My name is Sachit, I am 27 years old')
        mid, _ = self.member('Sachit')
        self.agent.backend = DemoBackend()
        result = self.send('I am having a fever')
        self.assertEqual(result['route'], 'ask')
        self.assertEqual(result['member_id'], mid)
        self.assertFalse(self.agent.state('household').get('registration'))
        self.member('Sachit')

    def test_i_am_coughing_is_not_a_new_person_named_coughing(self):
        self.send('My name is Sachit, I am 27 years old')
        mid, _ = self.member('Sachit')
        self.agent.backend = DemoBackend()
        result = self.send('I am coughing')
        self.assertEqual(result['route'], 'ask')
        self.assertEqual(result['member_id'], mid)
        self.assertFalse(self.agent.state('household').get('registration'))
        self.member('Sachit')


if __name__ == '__main__':
    unittest.main()
