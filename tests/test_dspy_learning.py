"""Budget tests are dependency-free; real DSPy compile tests run in the optional QA venv."""
import importlib.util,unittest
from dspy_learning import ProviderBudget

class OptimizerBudgetTests(unittest.TestCase):
    def test_missing_transport_refuses_before_call(self):
        with self.assertRaises(RuntimeError):ProviderBudget().call([])
    def test_eight_conservative_calls_and_ninth_refused(self):
        seen=[]
        def provider(**kw):seen.append(kw);return {'model':'gpt-5-mini','text':'fixture','usage':{'input_tokens':400000,'output_tokens':5000}}
        b=ProviderBudget(provider,authorized=True)
        for _ in range(8):b.call([])
        with self.assertRaises(RuntimeError):b.call([])
        self.assertEqual(len(seen),8);self.assertEqual(str(b.reserved),'0.88');self.assertEqual(seen[0]['retries'],0);self.assertEqual(seen[0]['tools'],[])
    def test_missing_usage_stops_and_retains_reserve(self):
        b=ProviderBudget(lambda **kw:{'text':'SENSITIVE_ECHO'},authorized=True)
        with self.assertRaises(RuntimeError) as error:b.call([])
        self.assertNotIn('SENSITIVE_ECHO',str(error.exception));self.assertEqual(str(b.reserved),'0.11')
        with self.assertRaises(RuntimeError):b.call([])
        self.assertEqual(len(b.calls),1)
    def test_existing_spend_and_context_bound(self):
        b=ProviderBudget(lambda **kw:self.fail('No dispatch'),authorized=True,spent_before='.85')
        with self.assertRaises(RuntimeError):b.call([])
        b=ProviderBudget(lambda **kw:self.fail('No dispatch'),authorized=True)
        with self.assertRaises(RuntimeError):b.call([{'content':'x'*100001}])

@unittest.skipUnless(importlib.util.find_spec('dspy'),'Optional DSPy dependency absent from base environment')
class ActualDSPyTests(unittest.TestCase):
    def test_real_labeled_compiler_uses_only_train_and_no_lm(self):
        from dspy_learning import compile_labeled
        proposal=compile_labeled([{'input':'synthetic train','expected':'reported'}],[{'input':'dev never copied','expected':'unknown'}])
        self.assertEqual(proposal['demos'],[{'input':'synthetic train','expected':'reported'}]);self.assertIn('no-LM',proposal['optimizer']);self.assertTrue(proposal['dspy_version'])
    def test_provider_optimizer_refuses_without_authorized_transport(self):
        from dspy_learning import compile_with_budgeted_optimizer
        with self.assertRaises(RuntimeError):compile_with_budgeted_optimizer([],[],budget=ProviderBudget())
