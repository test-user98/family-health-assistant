"""Social turns must be fast and must not invent family intent or lose intake."""
import copy
import unittest

from runtime import Runtime
from tests.test_runtime import ScriptedClient, envelope, intake, profile, followup


class SmalltalkTests(unittest.TestCase):
    def runtime(self, client=None):
        runtime = Runtime(':memory:', client)
        self.addCleanup(runtime.close)
        return runtime

    def test_greetings_and_typo_smalltalk_need_no_provider_or_search(self):
        client = ScriptedClient()
        runtime = self.runtime(client)
        for text in ('hi', 'Hi!', 'Hello dude', 'how is ti going dued', 'How’s it going, bro?', 'Thanks!'):
            with self.subTest(text=text):
                result = runtime.chat('family', text)
                self.assertEqual(result['route'], 'general')
                self.assertIsNone(result['member_id'])
                self.assertEqual(result['trace']['calls'], [])
                self.assertIn('social_fast_path', result['trace']['guardrails'])
        self.assertEqual(client.calls, [])
        self.assertEqual(runtime.state('family')['members'], {})

    def test_missing_key_allows_only_known_social_turns(self):
        runtime = self.runtime()
        self.assertEqual(runtime.chat('family', 'hi')['route'], 'general')
        self.assertEqual(runtime.chat('family', 'What can cause a cough?')['route'], 'setup_required')

    def test_vocative_after_relationship_is_not_a_name_even_if_model_proposes_it(self):
        for text, name in (("It's my father dued", 'Dued'), ('My father dude', 'Dude')):
            with self.subTest(text=text):
                client = ScriptedClient(envelope(intake(intent='register', profile_updates=[
                    profile(name=name, name_evidence=name.lower())])),
                    envelope({'message': 'How old is your father?', 'source_ids': []}))
                runtime = self.runtime(client)
                result = runtime.chat('family', text)
                self.assertEqual(result['route'], 'register')
                father = next(iter(runtime.state('family')['members'].values()))
                self.assertEqual(father['name'], 'Father')
                self.assertFalse(father['name_confirmed'])

    def test_social_interruption_preserves_the_pending_clinical_question(self):
        client = ScriptedClient(envelope(intake(intent='symptom', topic='fever',
            profile_updates=[profile(age=552, age_evidence='46')])),
            followup(observations=[dict(key='red_flags', value=False, evidence='No')]))
        runtime = self.runtime(client)
        runtime.chat('family', 'My father is 46 and has fever')
        before = copy.deepcopy(runtime.state('family'))
        result = runtime.chat('family', 'how is ti going dued')
        after = runtime.state('family')
        for field in ('members', 'episodes', 'active_member_id', 'active_episode_id', 'question_key'):
            self.assertEqual(before[field], after[field])
        self.assertEqual(result['route'], 'general')
        next_turn = runtime.chat('family', 'No')
        episode = runtime.state('family')['episodes'][next_turn['episode_id']]
        self.assertIs(episode['observations']['red_flags']['value'], False)
        self.assertEqual(runtime.state('family')['question_key'], 'duration_hours')
        self.assertEqual(len(client.calls), 2)

    def test_legacy_vocative_is_repaired_but_confirmed_proper_names_survive(self):
        for confirmed in (False, True):
            with self.subTest(confirmed=confirmed):
                runtime = self.runtime(ScriptedClient())
                state = runtime.state('family')
                state['members']['father'] = dict(name='Dued', relationship='father',
                    name_confirmed=confirmed, aliases=['father', 'dad', 'Dued'],
                    pronouns=['he', 'him'], age_months=552, facts={})
                runtime.store._save('family', state, None)
                runtime.chat('family', 'hi')
                father = runtime.state('family')['members']['father']
                self.assertEqual(father['name'], 'Dued' if confirmed else 'Father')
                if not confirmed:
                    self.assertNotIn('Dued', father['aliases'])

    def test_general_question_detour_does_not_lose_the_pending_triage_answer(self):
        client = ScriptedClient(envelope(intake(intent='symptom', topic='fever',
            profile_updates=[profile(age=552, age_evidence='46')])),
            envelope(intake()), envelope({'message': 'I help track family health information.', 'source_ids': []}),
            followup(observations=[dict(key='red_flags', value=False, evidence='No')]))
        runtime = self.runtime(client)
        runtime.chat('family', 'My father is 46 and has fever')
        runtime.chat('family', 'What is your role in this chat?')
        self.assertEqual(runtime.state('family')['question_key'], 'red_flags')
        result = runtime.chat('family', 'No')
        self.assertEqual(result['route'], 'ask')
        self.assertEqual(runtime.state('family')['question_key'], 'duration_hours')

    def test_greeting_prefix_does_not_hide_emergency_or_health_reports(self):
        runtime = self.runtime(ScriptedClient())
        self.assertEqual(runtime.chat('family', 'Hi, my father cannot breathe')['route'], 'emergency')
        # Unexpected model output fails closed; this is not accepted as a social turn.
        clinical = runtime.chat('another-family', 'Hey, my father has fever')
        self.assertEqual(clinical['route'], 'error')
        self.assertTrue(clinical['trace']['calls'])

    def test_smalltalk_does_not_clear_a_preexisting_emergency_hold(self):
        runtime = self.runtime(ScriptedClient())
        runtime.chat('family', 'Someone cannot breathe')
        self.assertEqual(runtime.chat('family', 'hi')['route'], 'general')
        self.assertTrue(runtime.state('family')['unassigned_emergency'])
