"""Source/contract regressions for the final five pinned Go compatibility findings.

These isolated tests are not a stateful Go-vs-Django acceptance test.
"""
import copy
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from cow.pricing_contract import Draft, Rule
from scripts.check_go_wire_contract import (
    _pinned_go_request_schema,
    _pinned_go_success_response,
    _success_response_refines,
)

ROOT = Path(__file__).resolve().parents[1]
FIVE = ROOT / 'docs/verification/go-python-parity-20261102/PARITY_REPORT_CHATGPT_RECOVERY_20261102T055047Z.json'
FLEET = {
    'POST /api/v1/admin/chargers/{charger_id}/maintenance',
    'POST /api/v1/admin/pairings',
    'POST /api/v1/admin/pairings/{pairing_id}/end',
    'POST /api/v1/admin/vehicles/{vehicle_id}/maintenance',
}

class FinalFiveStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.findings = json.loads(FIVE.read_text(encoding='utf-8'))['discrepancies']

    def test_exact_four_unversioned_fleet_responses_are_refinements(self):
        for d in self.findings:
            if d['operation'] not in FLEET:
                continue
            with self.subTest(operation=d['operation']):
                self.assertIn('ETag',d['go']['headers'])
                self.assertNotIn('ETag',d['python']['headers'])
                effective = _pinned_go_success_response(d['operation'],d['go'])
                self.assertNotIn('ETag',effective['headers'])
                self.assertTrue(_success_response_refines(effective,d['python']))
                self.assertIn('ETag',d['go']['headers'])  # did not mutate stored report

    def test_other_operations_must_not_have_etag_silenced(self):
        d = next(i for i in self.findings if i['operation'] in FLEET)
        changed = copy.deepcopy(d['go'])
        self.assertIn('ETag',_pinned_go_success_response('POST /api/v1/admin/vehicles',changed)['headers'])
        bad = copy.deepcopy(d['python'])
        bad['content']['application/json']['properties']['data']['properties'].pop('id')
        self.assertFalse(_success_response_refines(_pinned_go_success_response(d['operation'],d['go']),bad))

    def test_pricing_go_yaml_source_correction_is_exact(self):
        d = next(i for i in self.findings if i['operation']=='POST /api/v1/admin/pricing/policies')
        corrected = _pinned_go_request_schema(d['operation'],d['go'])['application/json']
        actual = copy.deepcopy(d['python']['application/json'])
        rule = actual['properties']['rules']['items']
        for name in ('component','basis','target','tax_class','conditions','priority'):
            t=rule['properties'][name]['type']
            if isinstance(t,str):
                rule['properties'][name]['type'] = sorted(['null',t])
        self.assertEqual(corrected,actual)
        self.assertEqual(d['go']['application/json']['properties']['rules']['items']['required'],['id','kind','priority','rate'])

    def test_go_null_and_empty_optional_rule_values(self):
        base = Rule.model_validate({
            'id':'BASE_1','kind':'BASE','rate':'10.00','component':'ENERGY',
            'basis':'KWH','tax_class':'GST','target':'',
            'priority':None,'conditions':None,
        })
        self.assertEqual(base.priority,0)
        self.assertEqual(base.conditions,{})
        self.assertIsNone(base.target)
        modifier = Rule.model_validate({
            'id':'MOD_1','kind':'DISCOUNT_PERCENT','rate':'10','target':'BASE_1',
            'component':'','basis':'','tax_class':'','priority':None,'conditions':None,
        })
        self.assertIsNone(modifier.component)
        self.assertIsNone(modifier.basis)
        self.assertIsNone(modifier.tax_class)
        self.assertEqual(modifier.priority,0)
        with self.assertRaises(ValidationError):
            Rule.model_validate({'id':'BASE_1','kind':'BASE','rate':'10','component':'ENERGY',
                                 'basis':'KWH','tax_class':'GST','target':'BASE_1'})
        with self.assertRaises(ValidationError):
            Rule.model_validate({'id':'BASE_1','kind':'BASE','rate':'10','component':'ENERGY',
                                 'basis':'KWH','tax_class':'GST','priority':-1})

    def test_pricing_declares_actual_null_semantics(self):
        defs = Draft.model_json_schema()['$defs']['Rule']['properties']
        for name in ('component','basis','target','tax_class','priority','conditions'):
            self.assertIn('null', [p.get('type') for p in defs[name].get('anyOf',[])],name)
        self.assertNotIn('priority',Draft.model_json_schema()['$defs']['Rule'].get('required',[]))

if __name__ == '__main__':
    unittest.main()
