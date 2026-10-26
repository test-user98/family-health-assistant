"""Source IDs constrain structure, not truth or semantic support."""
import unittest
import jsonschema
from runtime import reply_schema
from retrieval import grounded_citations,source_record

class CitationSupportTests(unittest.TestCase):
    def source(self):
        return source_record({'url':'https://www.nhs.uk/test-fixture','title':'Synthetic source fixture',
            'content':'Sleep difficulties can have different causes.','kind':'fetched'},1,100)

    def test_valid_id_does_not_make_an_invented_supporting_quote_valid(self):
        source=self.source();answer={'message':'This cures every headache.','source_ids':[1],
            'citations':[{'source_id':1,'quote':'This cures every headache.'}]}
        jsonschema.validate(answer,reply_schema([source]))  # Structurally valid, semantically ungrounded.
        rendered,chosen=grounded_citations(answer,[source],'Explain common headache causes.')
        self.assertEqual(chosen,[]);self.assertNotIn('cures every headache',rendered)
        self.assertIn('could not verify supporting passages',rendered)

    def test_valid_quote_does_not_preserve_an_unsupported_model_claim(self):
        source=self.source();answer={'message':'This proves that headaches are always harmless.',
            'source_ids':[1],'citations':[{'source_id':1,'quote':source['content']}]}
        jsonschema.validate(answer,reply_schema([source]))
        rendered,chosen=grounded_citations(answer,[source],'Explain headache uncertainty.')
        self.assertEqual(len(chosen),1);self.assertIn(source['content'],rendered)
        self.assertNotIn('always harmless',rendered);self.assertNotIn('This proves',rendered)
        # This only verifies passage provenance and suppression of the unsupported
        # claim, not clinical truth, relevance or general semantic entailment.
