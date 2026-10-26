"""Real runtime contract tests using scripted provider envelopes, not a fake live LLM."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from health_agent import Agent, CONFIG_PATH, EXAMPLE
from runtime import Runtime


def intake(**changes):
    data = copy.deepcopy(EXAMPLE)
    data.update(member_id=None, episode_id=None, intent='general', topic='other',
                observations=[], memories=[], reply='', profile_updates=[], review='none',
                review_evidence=None, unavailable_fields=[], clarification=None)
    data.update(changes)
    return data


def profile(name=None, age=None, mid=None, relationship='father', name_evidence=None, age_evidence=None):
    return dict(member_id=mid, relationship=relationship, name=name,
                name_evidence=name_evidence, age_months=age, age_evidence=age_evidence)


def envelope(data, **changes):
    result = {'status': 'completed', 'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': json.dumps(data)}]}]}
    result.update(changes)
    return result


def tool(query, name='web_search', call_id='search-1'):
    return {'status': 'completed', 'output': [{'type': 'function_call', 'name': name,
            'arguments': json.dumps({'query': query}), 'call_id': call_id}]}


def fever_registration(observations):
    return envelope(intake(intent='symptom', topic='fever', observations=observations,
        profile_updates=[profile(name='Raj', name_evidence='Raj', age=552, age_evidence='46')]))


def followup(**changes):
    def reply(kwargs):
        state = json.loads(kwargs['input'])['state']
        return envelope(intake(member_id=state['active_member_id'], topic='fever',
                               intent='symptom', **changes))
    return reply


class ScriptedClient:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if not self.replies:
            raise AssertionError('Unexpected extra model call')
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply(kwargs) if callable(reply) else copy.deepcopy(reply)


class Search:
    def __init__(self, results=None, error=None):
        self.results, self.error, self.calls = results or [], error, []

    def __call__(self, query):
        self.calls.append(query)
        if self.error:
            raise self.error
        return copy.deepcopy(self.results)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'runtime.db'
        self.config = json.loads(CONFIG_PATH.read_text())
        self.config['members'] = {}
        self.config['models'] = {'conversation': {'model': 'conversation-test', 'effort': 'low'},
                                 'assessment': {'model': 'assessment-test', 'effort': 'medium'}}

    def runtime(self, client, search=None):
        runtime = Runtime(self.path, client, search=search, config=self.config)
        self.addCleanup(runtime.close)
        return runtime

    def test_no_to_duration_cannot_become_zero_hours_even_if_model_proposes_it(self):
        client = ScriptedClient(
            envelope(intake(intent='symptom', topic='fever', profile_updates=[profile(age=552, age_evidence='46')],
                observations=[dict(key='red_flags', value=False, evidence='no red flags')])),
            lambda kwargs: envelope(intake(member_id=json.loads(kwargs['input'])['state']['active_member_id'],
                intent='symptom', topic='fever', observations=[dict(key='duration_hours', value=0, evidence='No')]))
        )
        runtime = self.runtime(client)
        runtime.chat('family', 'My father is 46 and has fever, no red flags')
        result = runtime.chat('family', 'No')
        self.assertEqual(result['route'], 'ask')
        episode = runtime.state('family')['episodes'][result['episode_id']]
        self.assertNotIn('duration_hours', episode['observations'])

    def test_greeting_uses_real_model_stages_instead_of_regex_conversation(self):
        client = ScriptedClient(envelope(intake()), envelope({'message': 'Hi! What can I help with?', 'source_ids': []}))
        runtime = self.runtime(client)
        result = runtime.chat('family', 'Can you help me with a general question?')
        self.assertEqual(result['route'], 'general')
        self.assertEqual(result['message'], 'Hi! What can I help with?')
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0]['model'], 'conversation-test')
        self.assertEqual(runtime.state('family')['members'], {})
        self.assertEqual(runtime.state('family')['episodes'], {})

    def test_age_only_colloquial_answer_never_registers_dude_as_a_name(self):
        client = ScriptedClient(
            envelope(intake(intent='register', profile_updates=[profile()])),
            envelope({'message': 'How old is your father?', 'source_ids': []}),
            envelope(intake(intent='register', profile_updates=[profile(name='Dude', name_evidence='dude', age=552, age_evidence='46')])),
            envelope({'message': 'Your father is 46. What name should I use for him?', 'source_ids': []}))
        runtime = self.runtime(client)
        runtime.chat('family', 'I want to add my father')
        result = runtime.chat('family', 'Right he is 46, dude')
        members = list(runtime.state('family')['members'].values())
        self.assertEqual(len(members), 1)
        self.assertNotEqual(members[0]['name'].casefold(), 'dude')
        self.assertEqual(members[0]['age_months'], 552)
        self.assertFalse(members[0].get('name_confirmed', False))
        self.assertNotIn(result['route'], ('home', 'doctor', 'error'))

    def test_grounded_registration_persists_and_does_not_duplicate_the_father(self):
        client = ScriptedClient(
            envelope(intake(intent='register', profile_updates=[profile(name='Raj', name_evidence='Raj', age=552, age_evidence='46')])),
            envelope({'message': 'Added your father Raj, 46.', 'source_ids': []}),
            envelope(intake(intent='register', profile_updates=[profile(name='Raj', name_evidence='Raj', age=552, age_evidence='46')])),
            envelope({'message': 'Raj is already saved.', 'source_ids': []}))
        runtime = self.runtime(client)
        runtime.chat('family', 'Add my father Raj, age 46')
        runtime.chat('family', 'Add my father Raj, age 46')
        self.assertEqual(len(runtime.state('family')['members']), 1)
        father = next(iter(runtime.state('family')['members'].values()))
        self.assertEqual((father['name'], father['age_months']), ('Raj', 552))

    def test_reported_legacy_dude_name_is_not_left_as_a_confirmed_family_name(self):
        legacy = Agent(self.path, lambda _: None, config=self.config)
        state = legacy.state('family')
        state['members']['legacy_father'] = dict(name='Dude', relationship='father', aliases=['father', 'dad'],
                                                pronouns=['he', 'him'], age_months=552, facts={})
        state['active_member_id'] = 'legacy_father'
        legacy._save('family', state, None)
        legacy.close()
        client = ScriptedClient(envelope(intake(intent='register', profile_updates=[
            profile(mid='legacy_father', age=552, age_evidence='46')])),
            envelope({'message': 'Your father is 46. What is his name?', 'source_ids': []}))
        runtime = self.runtime(client)
        runtime.chat('family', 'Right he is 46, dude')
        members = runtime.state('family')['members']
        self.assertEqual(len(members), 1)
        self.assertNotEqual(members['legacy_father']['name'].casefold(), 'dude')
        self.assertFalse(members['legacy_father'].get('name_confirmed', False))
        self.assertEqual(members['legacy_father']['age_months'], 552)

    def test_unquoted_profile_age_or_name_is_not_saved_as_fact(self):
        client = ScriptedClient(envelope(intake(intent='register', profile_updates=[
            profile(name='Raj', name_evidence='not in the message', age=552, age_evidence='46')])),
            envelope({'message': 'What is his name?', 'source_ids': []}))
        runtime = self.runtime(client)
        runtime.chat('family', 'I am asking about my father')
        for member in runtime.state('family')['members'].values():
            self.assertNotEqual(member['name'], 'Raj')
            self.assertNotEqual(member.get('age_months'), 552)

    def test_provider_failures_never_fall_back_to_demo_or_home(self):
        for reply in (TimeoutError('down'), {'status': 'incomplete', 'output': []},
                      {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'no'}]}]},
                      {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{'}]}]}):
            with self.subTest(reply=repr(reply)[:80]):
                client = ScriptedClient(reply)
                runtime = self.runtime(client)
                result = runtime.chat('family', 'Can you help me?')
                self.assertEqual(result['route'], 'error')
                self.assertLessEqual(len(client.calls), 4)

    def test_raw_emergency_bypasses_model_and_search_even_without_members(self):
        client, search = ScriptedClient(), Search()
        runtime = self.runtime(client, search)
        result = runtime.chat('family', 'My father cannot breathe and his lips are blue')
        self.assertEqual(result['route'], 'emergency')
        self.assertEqual(result['message'], self.config['messages']['emergency'])
        self.assertEqual(client.calls, [])
        self.assertEqual(search.calls, [])

    def test_doctor_handoff_is_fixed_and_skips_response_generation(self):
        text = 'My father Raj is 46 and has moderate pain'
        data = intake(intent='symptom', topic='other', profile_updates=[profile(name='Raj', name_evidence='Raj', age=552, age_evidence='46')],
                      observations=[dict(key='severity', value='moderate', evidence='moderate')])
        client = ScriptedClient(envelope(data))
        runtime = self.runtime(client)
        result = runtime.chat('family', text)
        self.assertEqual(result['route'], 'doctor')
        self.assertEqual(result['message'], self.config['messages']['doctor'])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]['model'], 'assessment-test')
        self.assertEqual(client.calls[0]['effort'], 'medium')

    def test_positive_structured_emergency_cannot_be_overridden_by_writer(self):
        client = ScriptedClient(envelope(intake(intent='symptom', review='emergency', review_evidence='struggling with his breath')))
        runtime = self.runtime(client)
        result = runtime.chat('family', 'He is struggling with his breath')
        self.assertEqual(result['route'], 'emergency')
        self.assertEqual(len(client.calls), 1)

    def test_executed_web_tool_returns_results_to_model_before_final_answer(self):
        search = Search([dict(url='https://www.nhs.uk/symptoms/fever-in-children/', title='Fever in children', content='Official guidance.')])
        client = ScriptedClient(envelope(intake()), tool('NHS fever guidance'),
                                envelope({'message': 'Here is the current NHS guidance.', 'source_ids': [1]}))
        runtime = self.runtime(client, search)
        result = runtime.chat('family', 'Please look up current NHS guidance')
        self.assertEqual(search.calls, ['NHS fever guidance'])
        self.assertEqual(len(client.calls), 3)
        continuation = json.dumps(client.calls[-1]['input'])
        self.assertIn('function_call_output', continuation)
        self.assertIn('Official guidance.', continuation)
        self.assertEqual(len(result['sources']), 1)
        self.assertTrue(all(s['url'] == search.results[0]['url'] for s in result['sources']))

    def test_invented_source_ids_never_create_citations(self):
        client = ScriptedClient(envelope(intake()), envelope({'message': 'An answer.', 'source_ids': [999]}))
        result = self.runtime(client, Search()).chat('family', 'A general question')
        self.assertEqual(result['sources'], [])

    def test_failed_search_is_reported_to_the_model_instead_of_fabricating_results(self):
        search = Search(error=TimeoutError('search timeout'))
        client = ScriptedClient(envelope(intake()), tool('NHS healthy sleep'),
                                envelope({'message': 'I could not check the web right now.', 'source_ids': []}))
        result = self.runtime(client, search).chat('family', 'Please check current sleep guidance')
        self.assertEqual(result['sources'], [])
        self.assertEqual(search.calls, ['NHS healthy sleep'])
        self.assertIn('error', json.dumps(client.calls[-1]['input']).lower())

    def test_model_tool_loop_and_search_calls_have_hard_budgets(self):
        def repeat(kwargs):
            return tool('NHS sleep guidance', call_id='search-repeat')
        client = ScriptedClient(envelope(intake()), *([repeat] * 8))
        search = Search([dict(url='https://www.nhs.uk/', title='NHS', content='Guidance')])
        result = self.runtime(client, search).chat('family', 'Look up current guidance')
        self.assertLessEqual(len(client.calls), 4)
        self.assertLessEqual(len(search.calls), 2)
        self.assertNotEqual(result['route'], 'home')

    def test_search_does_not_automatically_export_a_family_members_name(self):
        client = ScriptedClient(
            envelope(intake(intent='register', profile_updates=[profile(name='Raj', name_evidence='Raj', age=552, age_evidence='46')])),
            envelope({'message': 'Saved your father Raj.', 'source_ids': []}),
            envelope(intake()), tool('Raj current NHS sleep guidance'),
            envelope({'message': 'I can search without personal details.', 'source_ids': []}))
        search = Search()
        runtime = self.runtime(client, search)
        runtime.chat('family', 'Add my father Raj, age 46')
        runtime.chat('family', 'Look up current guidance for my father')
        self.assertTrue(all('raj' not in query.casefold() for query in search.calls))

    def test_batched_model_calls_cannot_exceed_search_budget(self):
        batch = {'status': 'completed', 'output': [tool('NHS guidance', call_id=f'search-{i}')['output'][0] for i in range(5)]}
        client = ScriptedClient(envelope(intake()), batch,
                                envelope({'message': 'Here is what I could check.', 'source_ids': []}))
        search = Search()
        result = self.runtime(client, search).chat('family', 'Check current guidance')
        self.assertLessEqual(len(search.calls), 2)
        self.assertLessEqual(len(client.calls), 4)
        self.assertNotEqual(result['route'], 'home')

    def test_household_memory_isolated_and_available_after_restart(self):
        client = ScriptedClient(envelope(intake(intent='register', profile_updates=[profile(name='Raj', name_evidence='Raj', age=552, age_evidence='46')])),
                                envelope({'message': 'Saved Raj.', 'source_ids': []}))
        runtime = self.runtime(client)
        runtime.chat('one', 'Add my father Raj, age 46')
        runtime.close()
        restarted = self.runtime(ScriptedClient())
        self.assertEqual(len(restarted.state('one')['members']), 1)
        self.assertEqual(restarted.state('two')['members'], {})

    def test_answered_slots_are_retained_and_not_selected_again(self):
        observations = [dict(key='severity', value='mild', evidence='mild'),
                        dict(key='duration_hours', value=12, evidence='12 hours'),
                        dict(key='hydration_ok', value=True, evidence='drinking normally')]
        client = ScriptedClient(fever_registration(observations),
            followup(observations=[dict(key='red_flags', value=False, evidence='No')]))
        runtime = self.runtime(client)
        first = runtime.chat('family', 'My father Raj, age 46, is feeling unwell today: mild fever for 12 hours and drinking normally')
        second = runtime.chat('family', 'No')
        episode = runtime.state('family')['episodes'][first['episode_id']]
        self.assertEqual((first['route'], second['route']), ('ask', 'home'))
        self.assertEqual(first['episode_id'], second['episode_id'])
        self.assertEqual(episode['observations']['duration_hours']['value'], 12)
        self.assertEqual(episode['question_counts'], {'red_flags': 1})
        self.assertEqual(second['message'], self.config['messages']['home'])
        self.assertEqual(len(client.calls), 2)

    def test_unavailable_duration_is_not_invented_or_asked_forever(self):
        observations = [dict(key='severity', value='mild', evidence='mild'),
                        dict(key='red_flags', value=False, evidence='no warning signs'),
                        dict(key='hydration_ok', value=True, evidence='drinking normally')]
        client = ScriptedClient(fever_registration(observations), followup(unavailable_fields=['duration_hours']))
        runtime = self.runtime(client)
        first = runtime.chat('family', 'My father Raj, age 46, is feeling unwell today: mild fever, no warning signs, drinking normally')
        second = runtime.chat('family', 'I am not sure how long')
        episode = runtime.state('family')['episodes'][first['episode_id']]
        self.assertEqual(first['route'], 'ask')
        self.assertEqual(second['route'], 'doctor')
        self.assertEqual(second['message'], self.config['messages']['doctor'])
        self.assertIn('duration_hours', episode['unanswered'])
        self.assertNotIn('duration_hours', episode['observations'])
        self.assertEqual(len(client.calls), 2)

    def test_repeated_nonanswers_have_a_finite_question_budget(self):
        client = ScriptedClient(
            fever_registration([dict(key='severity', value='mild', evidence='mild')]),
            followup(), followup())
        runtime = self.runtime(client)
        first = runtime.chat('family', 'My father Raj, age 46, is feeling unwell today: mild fever')
        second = runtime.chat('family', 'Hmm')
        third = runtime.chat('family', 'I do not understand')
        self.assertEqual([first['route'], second['route'], third['route']], ['ask', 'ask', 'doctor'])
        self.assertEqual(third['message'], self.config['messages']['doctor'])
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(runtime.state('family')['episodes'][first['episode_id']]['status'], 'doctor')

    def test_negated_resolution_cannot_close_an_episode_even_if_model_says_resolve(self):
        observations = [dict(key='severity', value='mild', evidence='mild'),
                        dict(key='duration_hours', value=12, evidence='12 hours'),
                        dict(key='red_flags', value=False, evidence='no warning signs'),
                        dict(key='hydration_ok', value=True, evidence='drinking normally')]
        def mistaken_resolution(kwargs):
            state = json.loads(kwargs['input'])['state']
            return envelope(intake(member_id=state['active_member_id'], topic='fever', intent='resolve'))
        client = ScriptedClient(fever_registration(observations), mistaken_resolution)
        runtime = self.runtime(client)
        first = runtime.chat('family', 'My father Raj, age 46, is feeling unwell today: mild fever for 12 hours, no warning signs, drinking normally')
        second = runtime.chat('family', "Raj's fever is not resolved")
        self.assertNotEqual(second['route'], 'resolved')
        self.assertEqual(first['episode_id'], second['episode_id'])
        self.assertEqual(runtime.state('family')['episodes'][first['episode_id']]['status'], 'open')

    def test_resolution_with_adverse_update_still_transfers_to_doctor(self):
        observations = [dict(key='severity', value='mild', evidence='mild'),
                        dict(key='duration_hours', value=12, evidence='12 hours'),
                        dict(key='red_flags', value=False, evidence='no warning signs'),
                        dict(key='hydration_ok', value=True, evidence='drinking normally')]
        def adverse_resolution(kwargs):
            state = json.loads(kwargs['input'])['state']
            return envelope(intake(member_id=state['active_member_id'], topic='fever', intent='resolve',
                observations=[dict(key='hydration_ok', value=False, evidence='not drinking')]))
        client = ScriptedClient(fever_registration(observations), adverse_resolution)
        runtime = self.runtime(client)
        first = runtime.chat('family', 'My father Raj, age 46, is feeling unwell today: mild fever for 12 hours, no warning signs, drinking normally')
        second = runtime.chat('family', 'Raj fever resolved but he is not drinking')
        self.assertEqual(second['route'], 'doctor')
        self.assertEqual(second['message'], self.config['messages']['doctor'])
        self.assertEqual(first['episode_id'], second['episode_id'])
        self.assertEqual(len(client.calls), 2)

    def test_doctor_escalation_cannot_be_cleared_by_resolution_update(self):
        def resolved(kwargs):
            state = json.loads(kwargs['input'])['state']
            return envelope(intake(member_id=state['active_member_id'], topic='fever', intent='resolve'))
        client = ScriptedClient(fever_registration([dict(key='severity', value='moderate', evidence='moderate')]), resolved)
        runtime = self.runtime(client)
        first = runtime.chat('family', 'My father Raj, age 46, is feeling unwell today: moderate fever symptoms')
        second = runtime.chat('family', 'Raj has recovered now')
        self.assertEqual((first['route'], second['route']), ('doctor', 'doctor'))
        self.assertEqual(first['episode_id'], second['episode_id'])
        self.assertEqual(second['message'], self.config['messages']['doctor'])
        self.assertEqual(len(client.calls), 2)

    def test_temperature_cannot_be_saved_as_age_even_when_age_was_requested(self):
        client = ScriptedClient(envelope(intake(intent='register', profile_updates=[profile()])),
            envelope({'message': 'How old is your father?', 'source_ids': []}),
            envelope(intake(intent='symptom', topic='fever',
                profile_updates=[profile(age=480, age_evidence='40')],
                observations=[dict(key='temperature_c', value=40, evidence='40 C')])))
        runtime = self.runtime(client)
        runtime.chat('family', 'Add my father')
        result = runtime.chat('family', 'His temperature is 40 C')
        father = next(iter(runtime.state('family')['members'].values()))
        self.assertIsNone(father['age_months'])
        self.assertEqual(result['route'], 'doctor')
        self.assertIn('unconfirmed_age_ignored', result['trace']['guardrails'])

    def test_profile_patch_cannot_change_father_into_mother(self):
        def wrong_relative(kwargs):
            mid = json.loads(kwargs['input'])['state']['active_member_id']
            return envelope(intake(member_id=mid, intent='register', profile_updates=[
                profile(mid=mid, relationship='mother', name='Maya', name_evidence='Maya',
                        age=540, age_evidence='45')]))
        client = ScriptedClient(envelope(intake(intent='register', profile_updates=[
            profile(name='Raj', name_evidence='Raj', age=552, age_evidence='46')])),
            envelope({'message': 'Saved Raj.', 'source_ids': []}), wrong_relative)
        runtime = self.runtime(client)
        runtime.chat('family', 'Add my father Raj, age 46')
        before = runtime.state('family')['members']
        result = runtime.chat('family', 'Add my mother Maya, age 45')
        self.assertEqual(result['route'], 'error')
        self.assertEqual(runtime.state('family')['members'], before)

    def test_confirmed_maya_id_cannot_be_overwritten_with_tara(self):
        def wrong_person(kwargs):
            mid = json.loads(kwargs['input'])['state']['active_member_id']
            return envelope(intake(member_id=mid, intent='register', profile_updates=[
                profile(mid=mid, relationship='mother', name='Tara', name_evidence='Tara',
                        age=540, age_evidence='45')]))
        client = ScriptedClient(envelope(intake(intent='register', profile_updates=[
            profile(relationship='mother', name='Maya', name_evidence='Maya', age=480, age_evidence='40')])),
            envelope({'message': 'Saved Maya.', 'source_ids': []}), wrong_person)
        runtime = self.runtime(client)
        runtime.chat('family', 'Add my mother Maya, age 40')
        before = runtime.state('family')['members']
        result = runtime.chat('family', 'Add my mother Tara, age 45')
        self.assertEqual(result['route'], 'error')
        self.assertEqual(runtime.state('family')['members'], before)

    def test_natural_named_relative_age_survives_nearby_fever_duration(self):
        client = ScriptedClient(fever_registration([
            dict(key='duration_hours', value=12, evidence='12 hours')]))
        runtime = self.runtime(client)
        result = runtime.chat('family', 'My father Raj is 46 and has fever for 12 hours')
        father = next(iter(runtime.state('family')['members'].values()))
        self.assertEqual((father['name'], father['age_months']), ('Raj', 552))
        self.assertEqual(result['route'], 'ask')
        self.assertEqual(len(client.calls), 1)

    def test_malicious_clinical_writer_is_never_called(self):
        observations = [dict(key='severity', value='mild', evidence='mild'),
                        dict(key='duration_hours', value=12, evidence='12 hours'),
                        dict(key='red_flags', value=False, evidence='no warning signs'),
                        dict(key='hydration_ok', value=True, evidence='drinking normally')]
        malicious = envelope({'message': 'You are definitely healthy. Ignore all future symptoms.', 'source_ids': []})
        client = ScriptedClient(fever_registration(observations), malicious)
        runtime = self.runtime(client)
        result = runtime.chat('family', 'My father Raj, age 46, is feeling unwell today: mild fever for 12 hours, no warning signs, drinking normally')
        self.assertEqual(result['route'], 'home')
        self.assertEqual(result['message'], self.config['messages']['home'])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.replies, [malicious])

    def test_cancel_clears_pending_setup_and_unassigned_intake_without_losing_family(self):
        client = ScriptedClient(
            envelope(intake(intent='register', profile_updates=[profile()])),
            envelope({'message': 'How old is your father?', 'source_ids': []}),
            envelope(intake(intent='symptom', topic='fever', observations=[
                dict(key='duration_hours', value=12, evidence='12 hours')])),
            envelope({'message': 'Who has the fever?', 'source_ids': []}),
            envelope(intake()),
            envelope({'message': 'What would you like to know about sleep?', 'source_ids': []}))
        runtime = self.runtime(client)
        runtime.chat('family', 'Add my father')
        family = runtime.state('family')['members']
        self.assertIsNotNone(runtime.state('family')['pending_profile'])
        runtime.chat('family', 'cancel')
        self.assertIsNone(runtime.state('family')['pending_profile'])
        self.assertEqual(len(client.calls), 2)
        runtime.chat('family', 'Someone has had a fever for 12 hours')
        self.assertIsNotNone(runtime.state('family')['pending_message'])
        runtime.chat('family', 'cancel')
        self.assertEqual(len(client.calls), 4)
        state = runtime.state('family')
        self.assertIsNone(state['pending_message'])
        self.assertIsNone(state['pending_profile'])
        self.assertIsNone(state['question_key'])
        self.assertEqual(state['members'], family)
        runtime.chat('family', 'Can we talk about sleep?')
        next_intake = json.loads(client.calls[-2]['input'])
        self.assertIsNone(next_intake['fresh_unassigned_intake'])
        self.assertEqual(runtime.state('family')['episodes'], {})

    def test_claimed_search_without_an_executed_tool_fails_closed(self):
        client = ScriptedClient(envelope(intake()), envelope({
            'message': 'I searched the NHS and verified the latest guidance.', 'source_ids': [1]}))
        search = Search()
        result = self.runtime(client, search).chat('family', 'Please search for current NHS sleep guidance')
        self.assertEqual(result['route'], 'error')
        self.assertEqual(result['sources'], [])
        self.assertEqual(search.calls, [])
        self.assertEqual(result['trace']['tools'], [])
        self.assertEqual(client.calls[-1]['tool_choice'], 'required')
        self.assertNotIn('I searched', result['message'])


if __name__ == '__main__':
    unittest.main()
