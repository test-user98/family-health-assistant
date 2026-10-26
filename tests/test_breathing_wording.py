"""Synthetic language regressions for the existing inability-to-breathe boundary."""
import json,tempfile,unittest
from pathlib import Path
from runtime import Runtime
from health_agent import emergency_signals
from human_review import HumanReview

class BreathingWordingTests(unittest.TestCase):
 def test_explicit_inability_variants_stop_before_model_or_review(self):
  config=json.loads(Path('config.json').read_text());config['human_review']={'enabled':True}
  def forbidden(**kwargs):raise AssertionError('Emergency must not wait for a model.')
  with tempfile.TemporaryDirectory() as d:
   for i,text in enumerate(('A fictional adult reports wheezing since last night and is not able to breath right.', 'I am not able to breathe properly.', 'She cannot breath.', 'He is unable to breathe.', "They can't breathe.", 'I am struggling to breathe.')):
    with self.subTest(text=text):
     runtime=Runtime(Path(d)/str(i),forbidden,config=config)
     HumanReview(runtime.store.db)
     try:
      result=runtime.chat('synthetic',text)
      self.assertEqual(result['route'],'emergency');self.assertEqual(result['message'],config['messages']['emergency']);self.assertEqual(result['trace']['calls'],[])
      self.assertEqual(runtime.store.db.execute('SELECT count(*) FROM human_cases').fetchone()[0],0)
     finally:runtime.close()
 def test_negation_history_and_education_not_treated_as_current_inability(self):
  config=json.loads(Path('config.json').read_text())
  for text in ('She is not struggling to breathe.', 'What if someone is not able to breathe?', 'He was unable to breathe last year.', 'She is wheezing but breathing normally.'):
   with self.subTest(text=text):self.assertEqual(emergency_signals(text,config),[])
