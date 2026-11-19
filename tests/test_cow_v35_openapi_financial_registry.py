"""Source-only and hermetic behavior regression for distinct Admin finance routes.

Can run `python -B tests/test_cow_v35_openapi_financial_registry.py` without
pytest/Django settings. Fully generated schema is checked independently.
"""
from __future__ import annotations
import ast
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
FINANCE={
    '/api/v1/admin/commercial/bookings/{booking_id}/approve-completion':'cowV3AdminApproveBookingCompletion',
    '/api/v1/admin/commercial/wallet-refunds':'cowV3AdminRequestWalletRefund',
    '/api/v1/admin/commercial/wallet-refunds/{refund_id}/approve':'cowV3AdminApproveWalletRefund',
}


def isolated_decorator():
    tree=ast.parse((ROOT/'cow/fe_openapi.py').read_text(encoding='utf-8'))
    statements=[]
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_V3_ADMIN_FINANCIAL_OPERATIONS' for t in node.targets):
            statements.append(node)
        elif isinstance(node,ast.FunctionDef) and node.name=='_enrich_commercial_operation':
            statements.append(node)
    assert len(statements)==2
    namespace={'deepcopy':__import__('copy').deepcopy}
    exec(compile(ast.fix_missing_locations(ast.Module(body=statements,type_ignores=[])),'fe_openapi.py','exec'),namespace)
    return namespace['_enrich_commercial_operation']


def operation(op_id):
    return {'operationId':op_id,'security':[{'AdminBearer':[]}],'x-fe-schema-detail':'declared',
            'requestBody':{'content':{'application/json':{'schema':{'$ref':'#/components/schemas/Review'}}}},
            'responses':{'200':{'content':{'application/json':{'schema':{'$ref':'#/components/schemas/Envelope'}}}}}}


class FinancialRegistryTests(unittest.TestCase):
    def setUp(self):
        self.enrich=isolated_decorator()
        self.fake=types.ModuleType('cow.common.contracts')
        self.fake.GO_ERROR_ENVELOPE_SCHEMA={'type':'object','properties':{'error':{'type':'object'}}}
        self.modules={'cow':types.ModuleType('cow'),'cow.common':types.ModuleType('cow.common'),
                      'cow.common.contracts':self.fake}

    def call(self,path,verb,obj):
        with patch.dict(sys.modules,self.modules):self.enrich(path,verb,obj)

    def test_all_three_financial_operations_are_different_from_tariff_configuration(self):
        for path,ident in FINANCE.items():
            with self.subTest(ident=ident):
                op=operation(ident)
                self.call(path,'post',op)
                self.assertEqual(op['operationId'],ident)
                self.assertIn('Idempotency-Key',[p['name'] for p in op['parameters']])
                self.assertTrue(op['parameters'][0]['required'])
                self.assertIn('503',op['responses'])
                self.assertNotIn('Reusable tariffs',op['description'])
                self.assertIn('wallet',op['description'].lower()) if 'Refund' in ident else self.assertIn('Financial closure',op['description'])

    def test_v3_rejects_wrong_id_wrong_verb_weak_auth_untyped_success_or_bad_json(self):
        path=list(FINANCE)[0]
        cases=[('get',operation(FINANCE[path])),('post',operation('adminCommercialTariffDraft'))]
        for verb,obj in cases:
            with self.assertRaises(ValueError):self.call(path,verb,obj)
        for field,value in [('security',[]),('x-fe-schema-detail','generic-success'),('requestBody',{})]:
            obj=operation(FINANCE[path]);obj[field]=value
            with self.assertRaises(ValueError):self.call(path,'post',obj)

    def test_v3_rejects_duplicate_or_optional_idempotency_header(self):
        path=list(FINANCE)[0]
        for params in [[{'in':'header','name':'Idempotency-Key','required':False}],
                       [{'in':'header','name':'Idempotency-Key','required':True}]*2]:
            obj=operation(FINANCE[path]);obj['parameters']=params
            with self.assertRaises(ValueError):self.call(path,'post',obj)

    def test_v3_finance_uses_distinct_frontend_tag(self):
        source=(ROOT/'cow/fe_openapi.py').read_text(encoding='utf-8')
        self.assertIn('37 | Admin booking closure and wallet refunds',source)
        tree=ast.parse(source)
        tag=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_tag')
        fn=ast.Module(body=[tag],type_ignores=[])
        fn=ast.fix_missing_locations(fn)
        ns={'_V3_ADMIN_FINANCIAL_OPERATIONS':FINANCE, 'PLANE':{}, 'AUTH_ACTIONS':{}}
        exec(compile(fn,'cow/fe_openapi.py','exec'),ns)
        for path in FINANCE:
            self.assertEqual(ns['_tag'](path),'37 | Admin booking closure and wallet refunds')

    def test_unknown_finance_route_still_fails_closed(self):
        with self.assertRaises(ValueError):
            self.call('/api/v1/admin/commercial/bookings/{booking_id}/secret','post',operation('cowV3AdminSecret'))

    def test_tariff_and_gst_existing_decorator_remains(self):
        for path,ident in [('/api/v1/admin/commercial/tariffs','adminCommercialTariffsList'),
                           ('/api/v1/admin/commercial/gst-profiles','adminCommercialGSTProfilesList')]:
            obj=operation(ident)
            self.call(path,'get',obj)
            self.assertIn('Reusable tariffs',obj['description'])
            self.assertEqual(obj.get('parameters'),[])

    def test_global_deepcopy_not_shadowed_in_commercial_decorator(self):
        # Regression for the exact Windows traceback: calling any financial
        # operation must not find a late, function-local deepcopy import.
        source=(ROOT/'cow/fe_openapi.py').read_text(encoding='utf-8')
        tree=ast.parse(source)
        global_copy=[node for node in tree.body if isinstance(node,ast.ImportFrom)
                     and node.module=='copy' and any(x.name=='deepcopy' for x in node.names)]
        self.assertEqual(len(global_copy),1)
        fun=next(x for x in tree.body if isinstance(x,ast.FunctionDef)
                 and x.name=='_enrich_commercial_operation')
        local_copy=[x for x in ast.walk(fun) if isinstance(x,ast.ImportFrom)
                    and x.module=='copy' and any(y.name=='deepcopy' for y in x.names)]
        self.assertEqual(local_copy,[], 'Function-local copy import shadows global deepcopy')

    def test_actual_admin_source_has_three_exact_post_routes(self):
        tree=ast.parse((ROOT/'cow/admin/nomap_routes.py').read_text(encoding='utf-8'))
        seen={}
        for node in ast.walk(tree):
            if not isinstance(node,ast.FunctionDef):continue
            for deco in node.decorator_list:
                if not isinstance(deco,ast.Call) or not isinstance(deco.func,ast.Attribute) or deco.func.attr!='post':continue
                if not deco.args or not isinstance(deco.args[0],ast.Constant):continue
                path=deco.args[0].value
                if not path.startswith('/commercial/'):continue
                op_id=next((kw.value.value for kw in deco.keywords if kw.arg=='operation_id' and isinstance(kw.value,ast.Constant)),None)
                seen['/api/v1/admin'+path]=op_id
        self.assertEqual(seen,FINANCE)


if __name__=='__main__':
    unittest.main(verbosity=2)
