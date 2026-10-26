"""Education must not invent an episode or latch a household emergency."""
import json
import unittest

from health_agent import CONFIG_PATH, current_symptoms, emergency_signals
from tests.test_runtime import ScriptedClient, envelope, intake
from runtime import Runtime


class IntentBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(CONFIG_PATH.read_text())

    def test_general_questions_reach_the_model_without_saving_acute_intake(self):
        for message in ('What causes fever in my child?',
                        'Does chest pain always mean a heart attack?',
                        'Why do people get chest pain?',
                        'Can someone have blue lips from cold weather?',
                        'If my father cannot breathe and his lips are blue, what should I do?',
                        'If my father cannot breathe, what should I do?'):
            with self.subTest(message=message):
                client = ScriptedClient(envelope(intake()), envelope({
                    'message': 'Here is general information, including when to seek help.', 'source_ids': []}))
                runtime = Runtime(':memory:', client, config=self.config)
                try:
                    result = runtime.chat('family', message)
                    self.assertEqual(result['route'], 'general')
                    state = runtime.state('family')
                    self.assertFalse(state.get('unassigned_emergency'))
                    self.assertFalse(state.get('pending_message'))
                    self.assertEqual(state['episodes'], {})
                    self.assertEqual(len(client.calls), 2)
                finally:
                    runtime.close()

    def test_questions_with_actual_danger_keep_the_immediate_emergency_path(self):
        for message in ('Why does my father have chest pain?',
                        'Can you help? Dad cannot breathe.',
                        'What is chest pain? My father has chest pain now.',
                        'If my father cannot breathe now, what should I do?',
                        'If someone gets chest pain, call for help. Dad cannot breathe.',
                        'What causes fever? But she has blue lips.'):
            with self.subTest(message=message):
                self.assertTrue(emergency_signals(message, self.config))

    def test_education_with_a_reported_symptom_remains_a_clinical_turn(self):
        self.assertFalse(current_symptoms('What causes fever in my child?'))
        self.assertTrue(current_symptoms('What causes fever? My child has fever now.'))
        self.assertTrue(current_symptoms('What should I do? Dad is breathless.'))
