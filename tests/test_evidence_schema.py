import json,unittest
from runtime import evidence_catalogue,intake_schema
from health_agent import CONFIG_PATH,validate
from tests.test_runtime import intake

class EvidenceSchemaTests(unittest.TestCase):
    def schema(self,source,state=None):
        cfg=json.loads(CONFIG_PATH.read_text());s=intake_schema(cfg['topics'],state,source)
        import jsonschema
        jsonschema.Draft202012Validator.check_schema(s)
        def enums(x):
            if isinstance(x,dict):
                if 'enum' in x:yield x['enum']
                for v in x.values():yield from enums(v)
            elif isinstance(x,list):
                for v in x:yield from enums(v)
        lists=list(enums(s));self.assertLessEqual(sum(len(e) for e in lists),1000)
        for values in lists:
            self.assertTrue(values);self.assertEqual(len(values),len({json.dumps(v,ensure_ascii=True) for v in values}))
            for v in values:
                if isinstance(v,str):self.assertNotIn('\n',v);self.assertNotIn('\r',v)
        return s

    def test_control_characters_and_line_separators_fall_back_without_sanitizing(self):
        for char in ['\n','\r','\t','\0','\x1b','\x7f','\x85','\u2028','\u2029','\ud800']:
            text='alpha'+char+'beta'
            with self.subTest(code=ord(char)):
                self.assertIsNone(evidence_catalogue(text));s=self.schema(text)
                self.assertNotIn('enum',s['properties']['memories']['items']['properties']['evidence'])
                # A joined/sanitized quote never becomes literal source evidence.
                bad={k:v for k,v in intake(memories=[dict(key='conditions',value='alphabeta',evidence='alphabeta')]).items() if k in ('member_id','episode_id','intent','topic','observations','memories','reply')}
                with self.assertRaises(ValueError):validate(bad,text,json.loads(CONFIG_PATH.read_text()))

    def test_quotes_backslashes_and_empty_sources_have_valid_plain_evidence(self):
        for source in ['', '   ', '?\n?', 'Mira said "no".',r'path\not a command']:
            self.assertIsNone(evidence_catalogue(source));self.schema(source)

    def test_unicode_keeps_exact_distinct_spans_and_roundtrips(self):
        for source in ['कृत्रिम परीक्षण, दर्द हल्का है।','Mira feels 🙂, replies 👩‍👩‍👧‍👦.','café, cafe\u0301.']:
            spans=evidence_catalogue(source);self.assertTrue(spans);self.assertTrue(all(x in source for x in spans));self.schema(source)
            self.assertEqual(json.loads(json.dumps(spans,ensure_ascii=False)),spans)
        spans=evidence_catalogue('café, cafe\u0301.');self.assertIn('café',spans);self.assertIn('cafe\u0301',spans)

    def test_large_catalogues_are_not_truncated_or_sanitized_into_enums(self):
        for source in ['x'*6000,','.join('literal-'+str(i) for i in range(2000))]:
            self.assertIsNone(evidence_catalogue(source));s=self.schema(source)
            self.assertNotIn('enum',s['properties']['memories']['items']['properties']['evidence'])
        state={'members':{str(i):{} for i in range(1200)},'episodes':{str(i):{} for i in range(1200)}}
        s=self.schema('A short synthetic source.',state);self.assertNotIn('enum',s['properties']['member_id'])

    def test_valid_catalogue_is_exact_deduplicated_bounded_and_negative_control_rejected(self):
        source='No fever, no fever. No fever'
        spans=evidence_catalogue(source);self.assertEqual(len(spans),len(set(spans)));self.assertLessEqual(len(spans),32);self.assertTrue(all(x in source for x in spans));self.schema(source)
        source='No\nfever';self.assertIsNone(evidence_catalogue(source));self.assertNotIn('No fever',source)
