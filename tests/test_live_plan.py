import contextlib,io,json,sys,unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from tests.test_openrouter_client import metadata
from openrouter_client import route

class LivePlanTests(unittest.TestCase):
    def test_reduced_generator_allowance_preserves_evaluation_and_total_cap(self):
        from qa_20261025 import live_ready
        r=route('test/model','test-route',lambda _:metadata());output=io.StringIO()
        args=['live_ready.py','--mode','optimize','--approved-total','5','--preflight']
        with patch.object(live_ready,'ROOT',Path(__file__).resolve().parents[1]/'qa_20261025'),patch.object(sys,'argv',args),patch.object(live_ready,'verify_build'),patch.object(live_ready,'guard_consumed_holdout'),patch.object(live_ready.ll,'previous_accounting',return_value=(Decimal('2.40'),True)),patch.object(live_ready,'route',return_value=r),patch.object(live_ready.ll,'project_key') as key,contextlib.redirect_stdout(output):live_ready.main()
        result=json.loads(output.getvalue());self.assertEqual(result['prior_accounted_usd'],'2.40');self.assertEqual(result['phase_cap_usd'],'3.10')
        self.assertLessEqual(Decimal(result['phase_cap_usd'])+Decimal(result['evaluation_room_reserved_for_optimization']),Decimal(result['total_working_ceiling']));key.assert_not_called()
