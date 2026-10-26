import json,tempfile,unittest,sqlite3
from pathlib import Path
from unittest.mock import patch
from learning import rubric_match,Learning,LearningError,digest
from health_agent import CONFIG_PATH

class ReviewedRubricTests(unittest.TestCase):
    def test_exported_holdout_seal_blocks_reuse_without_private_registry(self):
        from qa_20261025 import live_ready
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);q=root/'qa';q.mkdir();cfg=json.loads(CONFIG_PATH.read_text())
            workflow=Learning(root/'planned.db',cfg)
            try:
                live_ready.ll.dataset(workflow);fingerprint=digest(workflow.examples({'holdout','safety'}))
            finally:workflow.close()
            (root/'evaluation_seals.json').write_text(json.dumps({'consumed_dataset_hashes':[fingerprint]}))
            with patch.object(live_ready,'ROOT',q):
                with self.assertRaisesRegex(RuntimeError,'already consumed'):live_ready.guard_consumed_holdout(cfg)
    def test_reviewed_alternatives_accept_absence_of_information_not_false_certainty(self):
        r={'required':[],'required_any':[['unknown',"don't have information",'no specific information']],'forbidden':['definitely safe'],'route':'general'}
        self.assertTrue(rubric_match(r,{'message':"I don't have information to provide.",'route':'general'}))
        self.assertFalse(rubric_match(r,{'message':'It is definitely safe although unknown.','route':'general'}))
        self.assertFalse(rubric_match(r,{'message':'unknown','route':'unsupported'}))
        self.assertFalse(rubric_match(r,{'message':'I know its name.','route':'general'}))

    def test_malformed_empty_alternatives_refused(self):
        with tempfile.TemporaryDirectory() as d:
            workflow=Learning(Path(d)/'db',json.loads(CONFIG_PATH.read_text()))
            try:
                with self.assertRaises(LearningError):workflow.curate(id='x',family='x',split='train',input='Synthetic',expected='unknown',rubric={'required':[],'required_any':[[]],'forbidden':[],'route':'general'},synthetic=True,reviewed=True)
            finally:workflow.close()

    def test_consumed_qa_holdout_is_detected_across_new_output_folders(self):
        from qa_20261025 import live_ready
        cfg=json.loads(CONFIG_PATH.read_text())
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'live_previous';p.mkdir();workflow=Learning(p/'synthetic_learning.db',cfg)
            try:
                live_ready.ll.dataset(workflow);fingerprint=digest(workflow.examples({'holdout','safety'}))
                with workflow.db:workflow.db.execute('INSERT INTO learning_holdout_runs VALUES (?,?)',(fingerprint,'synthetic-candidate'))
            finally:workflow.close()
            with patch.object(live_ready,'ROOT',root):
                with self.assertRaisesRegex(RuntimeError,'already consumed'):live_ready.guard_consumed_holdout(cfg)
