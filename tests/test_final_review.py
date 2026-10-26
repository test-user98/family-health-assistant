"""Independent final review cases beyond the first holdout set."""
import unittest
from tests import test_continuity_features as features
from tests.test_runtime import ScriptedClient,envelope,intake
from continuity import record_context
class FinalReview(unittest.TestCase):
    setUp=features.ContinuityFeatures.setUp
    send=features.ContinuityFeatures.send
    safe=features.ContinuityFeatures.safe
    educate=features.ContinuityFeatures.educate
    def test_negative_style_qualifier_does_not_invert_preference(self):
        state={};record_context(state,'Keep answers detailed, not short.',1);self.assertEqual(state['context_records'][0]['value'],'detailed')
    def test_bilateral_report_is_not_reduced_to_last_side(self):
        state={};record_context(state,'Nisha has left ankle pain and right ankle pain.',1,'m','episode');rows=[r for r in state['context_records'] if r['key']=='ankle_side'];self.assertEqual(rows[0]['value'],'both')
    def test_generic_other_episodes_split_by_user_named_body_site(self):
        ids=[]
        for part in ['ankle','wrist']:
            text='Nisha has mild '+part+' pain for 2 hours, no red flags, drinking normally.';self.r.client=ScriptedClient(envelope(intake(member_id='m',intent='symptom',topic='other',observations=[dict(key=k,value=v,evidence=text) for k,v in {'severity':'mild','duration_hours':2,'red_flags':False,'hydration_ok':True}.items()])));ids.append(self.r.chat('a',text)['episode_id'])
        self.assertNotEqual(*ids);self.assertEqual({e['body_site'] for e in self.r.state('a')['episodes'].values()},{'ankle','wrist'})
    def test_retrospective_laterality_does_not_replace_current_side(self):
        state={};record_context(state,'Nisha has right knee pain.',1,'m','episode');record_context(state,'Yesterday Nisha had left knee pain.',2,'m','episode');active=[r for r in state['context_records'] if r['key']=='knee_side' and r['status']=='active'];self.assertEqual(active[0]['value'],'right')
    def test_shared_gender_without_explicit_member_never_changes_other_person(self):
        self.safe();out,_=self.send('She has new pain.',mid='s');self.assertEqual(out['route'],'clarify_member')
    def test_cache_bypass_on_general_turn_with_profile_memory_update(self):
        self.educate();out,c=self.send('Explain wool allergy. Nisha is allergic to wool.',intent='general',memories=[dict(key='allergies',value='wool',evidence='allergic to wool')]);self.assertEqual(out['trace']['cache']['status'],'bypass')
    def test_question_about_style_does_not_invent_preference(self):
        self.educate('What are concise answers?');self.assertFalse(any(r['key']=='answer_style' for r in self.r.state('a')['context_records']))
