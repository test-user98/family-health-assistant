import unittest
from unittest.mock import patch
from dspy_learning import compile_with_budgeted_optimizer

class OptimizerPreflightTests(unittest.TestCase):
    def test_missing_optional_dependency_refuses_before_any_provider_call(self):
        import dspy  # Load DSPy before narrowly simulating the missing optimizer extra.
        class Budget:
            authorized=True;provider=True;calls=0
            def call(self,messages):self.calls+=1;raise AssertionError('Provider must not run')
        budget=Budget()
        with patch('importlib.import_module',side_effect=ModuleNotFoundError('synthetic missing optuna')):
            with self.assertRaisesRegex(RuntimeError,'optuna is required before provider dispatch'):
                compile_with_budgeted_optimizer([{'input':'synthetic','expected':'unknown'}],[{'input':'dev','expected':'unknown'}],budget=budget)
        self.assertEqual(budget.calls,0)
