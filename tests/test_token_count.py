import copy,json,tempfile,unittest
from unittest.mock import patch
from token_count import cached_o200k,meter
from memory_context import retrieve,encoded,ContextOverflow
from health_agent import CONFIG_PATH
from qa_20261025.model_benchmark import seed
from runtime import Runtime
from pathlib import Path

class TokenCountTests(unittest.TestCase):
    def test_cached_encoder_matches_official_tokenizer_without_network(self):
        encoder=cached_o200k()
        if encoder is None:self.skipTest('Optional tokenizer data not prepared; fallback tested separately.')
        import tiktoken
        with patch.dict('os.environ',{'TIKTOKEN_CACHE_DIR':'/tmp/family-health-tokenizer-cache'}),patch('tiktoken.load.read_file',side_effect=AssertionError('Unexpected fetch')):
            official=tiktoken.encoding_for_model('gpt-5-mini')
        for text in ['Meera is 53, not 52.','यह एक कृत्रिम परीक्षण है।','<|endoftext|> is untrusted user text.']:
            self.assertEqual(encoder.encode(text,disallowed_special=()),official.encode(text,disallowed_special=()))

    def test_unknown_model_and_missing_data_keep_conservative_counter(self):
        cfg={'context':{'token_counter':'cached_tiktoken'},'models':{'conversation':{'model':'other/unknown'}}}
        count,name=meter(cfg,encoded);self.assertEqual(name,'utf8_byte_upper_bound');self.assertEqual(count({'x':'abc'}),len(encoded({'x':'abc'})))
        cfg['models']['conversation']['model']='gpt-5-mini'
        with patch('token_count.cached_o200k',return_value=None):self.assertEqual(meter(cfg,encoded)[1],'utf8_byte_upper_bound')

    def test_actual_overflow_state_fits_token_budget_without_losing_critical_observations(self):
        if cached_o200k() is None:self.skipTest('Optional tokenizer data not prepared.')
        cfg=json.loads(CONFIG_PATH.read_text());cfg['models']={'conversation':{'model':'gpt-5-mini'}};cfg['context']={'token_counter':'utf8'}
        with tempfile.TemporaryDirectory() as d:
            r=Runtime(Path(d)/'db',None,config=cfg);seed(r);state=r.state('benchmark-synthetic');r.close()
        episode=state['episodes']['current_cough']
        for item in episode['observations'].values():item['evidence']='Mira reported the same supplied symptom context. '*20
        with self.assertRaises(ContextOverflow):retrieve(state,'Mira cough',cfg)
        cfg['context']={'token_counter':'cached_tiktoken'};out=retrieve(state,'Mira cough',cfg)
        self.assertEqual(out['episodes']['current_cough']['observations'],episode['observations'])
        self.assertIn('latex',json.dumps(out));self.assertEqual(out['memory_manifest']['token_counter'],'cached_tiktoken_o200k_base')
        self.assertLessEqual(out['memory_manifest']['token_upper_bound'],3000)
