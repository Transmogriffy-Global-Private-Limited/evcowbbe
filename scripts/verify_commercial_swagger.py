#!/usr/bin/env python3
"""Offline public Swagger/OpenAPI acceptance for unified commercial Admin API."""
import json, os
from scripts.db import offline_env
os.environ.update(offline_env())
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
import django
django.setup()
from django.test import Client
from cow.api import api
import yaml

P='/api/v1/admin/commercial/'
s=api.get_openapi_schema()
assert s['x-commercial-tariff-mode']=='reusable-library-with-legacy-zone'
assert '### Commercial tariff authority (current)' in s['info']['description']
assert '### Tariff authority: Zone composite only' not in s['info']['description']
operations=[]
for path,item in s['paths'].items():
    if not path.startswith(P): continue
    for method,op in item.items():
        if method.lower() not in {'get','post','patch','put','delete'}: continue
        oid=op.get('operationId')
        assert oid and oid.startswith('adminCommercial'), (method,path,oid)
        assert op.get('security'), (method,path,'Admin security')
        assert op.get('x-fe-schema-detail')=='declared', (method,path,'typed response')
        assert op.get('tags',[None])[0]!='23 | Other development endpoints', (method,path,'tag')
        headers={p['name']:p for p in op.get('parameters',[]) if p.get('in')=='header'}
        if method.lower()=='post':
            assert 'application/json' in op.get('requestBody',{}).get('content',{}),path
            if path!=P+'preview':
                assert headers.get('Idempotency-Key',{}).get('required') is True,path
        if oid=='adminCommercialTariffPublish':
            assert headers.get('If-Match',{}).get('required') is True,path
        assert any(str(k).startswith('2') for k in op.get('responses',{})),path
        operations.append(oid)
assert len(operations)==24 and len(set(operations))==24,len(operations)
assert not any(p.startswith('/api/v1/admin/pricing/policies') for p in s['paths'])
for p in ('/api/v1/app/service-requests/{request_id}/quote','/api/v1/app/quotes/{quote_id}/accept'):
    assert p not in s['paths']
c=Client()
a=c.get('/api/openapi.json')
b=c.get('/openapi.yaml')
w=c.get('/api/swagger/')
assert (a.status_code,b.status_code,w.status_code)==(200,200,200), (a.status_code,b.status_code,w.status_code)
assert json.loads(a.content)==yaml.safe_load(b.content),'JSON/YAML drift'
print('COMMERCIAL_SWAGGER=PASS; OPERATIONS=24; JSON_YAML=PASS; SWAGGER_UI=200')
