"""Conversation contracts; these fixtures do not measure real-model intelligence."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluate import build_scenarios, run_evaluation


class ConversationEvaluationTests(unittest.TestCase):
    def test_catalog_has_sixty_distinct_cases_and_three_long_memory_conversations(self):
        cases = build_scenarios()
        self.assertEqual(len(cases), 60)
        self.assertEqual(len({case['name'] for case in cases}), 60)
        long = [case for case in cases if case['category'] == 'long_memory']
        self.assertEqual(len(long), 3)
        for case in long:
            self.assertGreaterEqual(sum(not turn.get('restart') for turn in case['turns']), 23)
            self.assertIn('distant_fact', case['turns'][-1]['checks'])
            self.assertTrue(any(turn.get('restart') for turn in case['turns']))

    def test_report_labels_fixture_evidence_and_writes_no_conversation_transcripts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'aggregate.json'
            report = run_evaluation(output=path, scenarios=build_scenarios()[:2])
            self.assertEqual(json.loads(path.read_text()), report)
            self.assertEqual(report['evidence'], 'harness_contracts_with_declared_model_outputs')
            self.assertEqual(report['status'], 'passed')
            self.assertEqual(len(list(Path(directory).iterdir())), 1)
            self.assertNotIn('profiles', json.dumps(report))

    def test_unavailable_live_provider_is_blocked_and_not_called_a_pass(self):
        with patch('evaluate.load_openai_key', return_value='test-placeholder'), \
             patch('evaluate.ResponsesClient', side_effect=RuntimeError('Provider unavailable')):
            report = run_evaluation('llm')
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['passed'], 0)
        self.assertEqual(report['skipped'], 60)
        self.assertEqual(report['evidence'], 'live_model_behavior')
        self.assertNotIn('test-placeholder', json.dumps(report))


def _scenario_test(scenario):
    def test(self):
        report = run_evaluation(scenarios=[scenario])
        self.assertEqual(report['status'], 'passed', report['results'])
        self.assertEqual(report['passed'], 1)
        self.assertEqual(report['failed'], 0)
    test.__doc__ = scenario['name'] + ' (declared interpretation, real runtime/state/policy).'
    return test


for _index, _case in enumerate(build_scenarios(), 1):
    setattr(ConversationEvaluationTests, f'test_conversation_{_index:02d}', _scenario_test(_case))


if __name__ == '__main__':
    unittest.main()
