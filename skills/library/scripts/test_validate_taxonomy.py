import copy
import unittest
from validate_taxonomy import validate


class TaxonomyTests(unittest.TestCase):
    def setUp(self):
        self.base = {'nodes': [
            {'id': 'parent', 'canonical_name': 'Example', 'page_id': 'existing'},
            {'id': 'child', 'canonical_name': 'Subtopic', 'parents': ['parent'], 'aliases': [{'name': 'Example child'}]}],
            'company_ids': ['company'],
            'company_topic_edges': [{'company': 'company', 'topic': 'child'}]}

    def test_valid(self):
        self.assertEqual(validate(self.base, self.base), [])

    def test_invariants(self):
        cases = [
            lambda x: x['nodes'].append(copy.deepcopy(x['nodes'][0])),
            lambda x: x['nodes'][1].update(parents=['missing']),
            lambda x: x['nodes'][0].update(parents=['child']),
            lambda x: x.update(crosslinks=[{'source': 'parent', 'target': 'missing'}]),
            lambda x: x['company_topic_edges'][0].update(company='missing'),
            lambda x: x['nodes'][1].update(canonical_name='ＥＸＡＭＰＬＥ'),
            lambda x: x['nodes'][1].update(aliases=[{'name': ' EXAMPLE '}]),
            lambda x: x['nodes'][0].update(page_id='changed'),
            lambda x: x['nodes'].pop(0),
        ]
        for mutate in cases:
            with self.subTest(mutate=mutate):
                candidate = copy.deepcopy(self.base)
                mutate(candidate)
                self.assertTrue(validate(candidate, self.base))

    def test_context_alias(self):
        self.base['nodes'][1]['aliases'] = [{'name': ' EXAMPLE ', 'context_only': True}]
        self.assertEqual(validate(self.base), [])


if __name__ == '__main__':
    unittest.main()
