"""Natural profile ages remain grounded in the user's actual age statement."""
import tempfile
import unittest
from pathlib import Path

from runtime import Runtime
from tests.test_runtime import ScriptedClient, envelope, intake, profile


class NaturalAgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.counter = 0

    def runtime(self, *responses):
        self.counter += 1
        agent = Runtime(Path(self.temp.name) / f'{self.counter}.db', ScriptedClient(*responses))
        self.addCleanup(agent.close)
        return agent

    def register(self, text, *, role, name, age, evidence, route='register'):
        update = profile(relationship=role, name=name, name_evidence=name,
                         age=age, age_evidence=evidence)
        agent = self.runtime(envelope(intake(intent='register', profile_updates=[update])),
                             envelope({'message': 'Saved the supplied family details.', 'source_ids': []}))
        result = agent.chat('family', text)
        self.assertEqual(result['route'], route, result)
        return next(iter(agent.state('family')['members'].values()))

    def test_contractions_accept_ascii_and_curly_apostrophes(self):
        for apostrophe in ("'", '’'):
            with self.subTest(apostrophe=apostrophe):
                member = self.register(f'I{apostrophe}m Sachit and I{apostrophe}m 27',
                    role='self', name='Sachit', age=324, evidence='27')
                self.assertEqual((member['name'], member['age_months']), ('Sachit', 324))
                father = self.register(f'Add my father Raj, he{apostrophe}s 46',
                    role='father', name='Raj', age=552, evidence='46')
                self.assertEqual(father['age_months'], 552)

    def test_spelled_ages_and_month_units(self):
        for text, role, name, age, evidence in (
            ('My daughter Maya is eight years old', 'daughter', 'Maya', 96, 'eight years old'),
            ('My father Raj is forty-six', 'father', 'Raj', 552, 'forty-six'),
            ('My father Raj is forty six', 'father', 'Raj', 552, 'forty six'),
            ('My father Raj is one hundred and two years old', 'father', 'Raj', 1224, 'one hundred and two'),
            ('My son Ari is two months old', 'son', 'Ari', 2, 'two months old'),
        ):
            with self.subTest(text=text):
                member = self.register(text, role=role, name=name, age=age, evidence=evidence)
                self.assertEqual(member['age_months'], age)

    def test_age_evidence_does_not_borrow_a_different_numeric_age_context(self):
        member = self.register('My father Raj is 46 and has fever for twelve hours',
            role='father', name='Raj', age=144, evidence='twelve hours', route='ask')
        self.assertIsNone(member['age_months'])

    def test_explicit_units_cannot_be_changed_by_the_model(self):
        member = self.register('My son Ari is two months old',
            role='son', name='Ari', age=24, evidence='two months old')
        self.assertIsNone(member['age_months'])

    def test_clinical_measurements_are_rejected_even_when_age_was_requested(self):
        for text, age, evidence in (
            ('His temperature is forty C', 480, 'forty'),
            ('His temperature is 40 °C', 480, '40'),
            ('He has had fever for eight days', 96, 'eight days'),
            ('The cough started twelve hours ago', 144, 'twelve'),
            ('He has been coughing since two months ago', 2, 'two months'),
        ):
            with self.subTest(text=text):
                agent = self.runtime(
                    envelope(intake(intent='register', profile_updates=[profile()])),
                    envelope({'message': 'How old is your father?', 'source_ids': []}),
                    envelope(intake(intent='register', profile_updates=[profile(age=age, age_evidence=evidence)])),
                    envelope({'message': 'His age is still unknown.', 'source_ids': []}))
                agent.chat('family', 'Add my father')
                agent.chat('family', text)
                self.assertIsNone(next(iter(agent.state('family')['members'].values()))['age_months'])

    def test_normalized_spelling_does_not_waive_literal_evidence(self):
        member = self.register('My daughter Maya is eight years old',
            role='daughter', name='Maya', age=96, evidence='8 years old')
        self.assertIsNone(member['age_months'])

    def test_quoted_age_can_include_sentence_punctuation(self):
        for evidence in ('46.', 'age 46.', 'Add my father Raj, age 46.'):
            with self.subTest(evidence=evidence):
                member = self.register('Add my father Raj, age 46.',
                    role='father', name='Raj', age=552, evidence=evidence)
                self.assertEqual(member['age_months'], 552)


if __name__ == '__main__':
    unittest.main()
