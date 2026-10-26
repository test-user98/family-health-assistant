"""Safety regressions from independent review of concrete conversational failures."""
import json
import unittest

from backends import DemoBackend
from health_agent import CONFIG_PATH, emergency_signals
from tests.fixtures import SeededAgent as Agent


SAFE_CHILD = 'Maya has mild fever for 3 hours, no red flags, drinking and urinating normally.'


class SafetyRegressionTests(unittest.TestCase):
    def setUp(self):
        self.agent = Agent(':memory:', DemoBackend())
        self.config = json.loads(CONFIG_PATH.read_text())

    def tearDown(self):
        self.agent.close()

    def chat(self, text, household='family'):
        return self.agent.chat(household, text)

    def test_positive_emergencies_survive_unrelated_negation_and_history(self):
        def unavailable(_):
            self.fail('The emergency fast path must not call the backend')
        self.agent.backend = unavailable
        messages = [
            'Dad has no fever despite blue lips.',
            'Dad has a history of asthma, he is gasping now.',
            'Dad is not only gasping, he is in distress.',
            'No fever, but Dad is gasping and cannot speak.',
            'Dad had slurred speech two hours ago, normal now.',
        ]
        for index, message in enumerate(messages):
            with self.subTest(message=message):
                result = self.chat(message, str(index))
                self.assertEqual(result['route'], 'emergency')
                self.assertEqual(result['message'], self.config['messages']['emergency'])

    def test_solicited_emergency_signs_have_immediate_action(self):
        for index, message in enumerate([
            'Maya is hard to wake.',
            'Maya has fever and a rash that does not fade when pressed.',
            'Dad is suddenly confused.',
            'Dad has new one-sided weakness.',
        ]):
            with self.subTest(message=message):
                self.assertEqual(self.chat(message, str(index))['route'], 'emergency')

    def test_denied_hypothetical_and_remote_signs_do_not_trigger_raw_emergency(self):
        for message in [
            'Dad has no blue lips.',
            "Dad doesn't have chest pain.",
            'Dad had slurred speech in 2018.',
            'What if someone has chest pain?',
        ]:
            with self.subTest(message=message):
                self.assertEqual(emergency_signals(message, self.config), [])

    def test_personal_questions_cannot_bypass_doctor_review(self):
        for index, message in enumerate([
            'What should I do? Dad is breathless.',
            'What should I do? Dad is getting worse.',
            'What do we do? My dad is not drinking.',
        ]):
            with self.subTest(message=message):
                result = self.chat(message, str(index))
                self.assertEqual(result['route'], 'doctor')
                self.assertEqual(result['message'], self.config['messages']['doctor'])

    def test_known_pronoun_emergency_latches_across_reassuring_update(self):
        self.assertEqual(self.chat(SAFE_CHILD)['route'], 'home')
        emergency = self.chat('She is gasping.')
        self.assertEqual(emergency['route'], 'emergency')
        followup = self.chat('She has mild symptoms for 3 hours, no red flags, drinking and urinating normally.')
        self.assertEqual(followup['route'], 'emergency')
        self.assertEqual(self.agent.state('family')['members']['child']['safety_hold'], 'emergency')

    def test_unresolved_emergency_identity_cannot_become_home_advice(self):
        self.assertEqual(self.chat('Someone is gasping.')['route'], 'emergency')
        self.assertEqual(self.chat(SAFE_CHILD)['route'], 'emergency')
        self.assertEqual(self.agent.state('family')['episodes'], {})

    def test_named_emergency_becomes_followup_focus(self):
        self.chat(SAFE_CHILD)
        self.assertEqual(self.chat('Dad is gasping.')['member_id'], 'parent')
        followup = self.chat('Still mild symptoms for 3 hours, no red flags, drinking and urinating normally.')
        self.assertEqual(followup['route'], 'emergency')
        self.assertEqual(followup['member_id'], 'parent')
        self.assertEqual(self.agent.state('family')['active_member_id'], 'parent')

    def test_safety_hold_does_not_leak_into_another_household(self):
        self.chat('Someone is gasping.')
        result = self.chat(SAFE_CHILD, 'different-family')
        self.assertEqual(result['route'], 'home')

    def test_negative_resolution_keeps_the_existing_episode_open(self):
        initial = self.chat(SAFE_CHILD)
        result = self.chat('Maya fever is not resolved.')
        self.assertNotEqual(result['route'], 'resolved')
        episode = self.agent.state('family')['episodes'][initial['episode_id']]
        self.assertEqual(episode['status'], 'open')

    def test_adverse_observation_beats_resolution(self):
        self.chat(SAFE_CHILD)
        result = self.chat('Maya fever has resolved but she is not drinking.')
        self.assertEqual(result['route'], 'doctor')
        self.assertEqual(result['message'], self.config['messages']['doctor'])

    def test_duration_advances_between_turns(self):
        clock = [1000.0]
        self.agent.clock = lambda: clock[0]
        self.assertEqual(self.chat('Maya has mild fever for 40 hours, no red flags, drinking and urinating normally.')['route'], 'home')
        clock[0] += 10 * 3600
        result = self.chat('Maya still has mild fever, no red flags, drinking and urinating normally.')
        self.assertEqual(result['route'], 'doctor')

    def test_infant_suspected_fever_needs_review_without_measurement(self):
        result = self.chat('My baby feels feverish, no thermometer available.')
        self.assertEqual(result['route'], 'doctor')
        self.assertIn('infant_fever', result['reasons'])

    def test_explicit_recurrence_creates_distinct_episode_and_followups_reuse_it(self):
        first = self.chat(SAFE_CHILD)
        second = self.chat('Maya has a new episode of mild fever for 2 hours, no red flags, drinking and urinating normally.')
        self.assertNotEqual(second['episode_id'], first['episode_id'])
        self.assertEqual(len(self.agent.state('family')['episodes']), 2)
        followup = self.chat('She still has mild fever, no red flags, drinking and urinating normally.')
        self.assertEqual(followup['episode_id'], second['episode_id'])

    def test_negated_recurrence_does_not_create_an_episode(self):
        first = self.chat(SAFE_CHILD)
        second = self.chat('Maya has the same fever, not a new episode. Still mild for 3 hours, no red flags, drinking and urinating normally.')
        self.assertEqual(second['episode_id'], first['episode_id'])
        self.assertEqual(len(self.agent.state('family')['episodes']), 1)

    def test_new_episode_cannot_downgrade_unresolved_doctor_review(self):
        self.assertEqual(self.chat('Maya has severe fever.')['route'], 'doctor')
        result = self.chat('Maya has a new episode of mild fever for 2 hours, no red flags, drinking and urinating normally.')
        self.assertEqual(result['route'], 'doctor')
        self.assertEqual(result['message'], self.config['messages']['doctor'])


if __name__ == '__main__':
    unittest.main()
