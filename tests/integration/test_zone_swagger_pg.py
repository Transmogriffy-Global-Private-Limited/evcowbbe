"""API contract acceptance under the isolated PostgreSQL test runner."""
from uuid import uuid4
import json
import pytest
from django.test import Client

from cow.api import api
from cow.models import Account, AdminRole, PricingPolicy, Principal
from cow.services import auth

pytestmark=[pytest.mark.postgres,pytest.mark.django_db]


def test_zone_only_openapi_documented():
    schema=api.get_openapi_schema()
    assert schema['x-commercial-tariff-mode']=='zone-composite-only'
    assert not any(p.startswith('/api/v1/admin/pricing/policies') for p in schema['paths'])
    assert '/api/v1/app/service-requests/{request_id}/quote' not in schema['paths']
    assert '/api/v1/app/quotes/{quote_id}/accept' not in schema['paths']
    assert '/api/v1/admin/zones/tariffs' in schema['paths']
    op=schema['paths']['/api/v1/admin/zones/tariffs']['post']
    assert op['operationId']=='adminZoneTariffDraft'
    assert op['x-fe-schema-detail']=='declared'
    assert any(p['name']=='Idempotency-Key' and p['required'] for p in op['parameters'])
    assert 'ZoneTariffPlan' in str(schema['components']['schemas'])
    published=schema['paths']['/api/v1/admin/zones/tariffs/{policy_id}/publish']['post']
    assert any(p['name']=='If-Match' and p['required'] for p in published['parameters'])


def test_legacy_pricing_urls_explicitly_retired():
    account=Account.objects.create(email=f'swagger-{uuid4().hex}@example.invalid',
        password_hash='x',email_verified=True)
    principal=Principal.objects.create(account=account,plane='admin',status='ACTIVE')
    AdminRole.objects.create(principal=principal,role='FULL_ADMIN')
    authz={'HTTP_AUTHORIZATION':'Bearer '+auth.make_tokens(principal)['access_token']}
    api_client=Client()
    endpoint='/api/v1/admin/pricing/policies'
    response=api_client.get(endpoint,**authz)
    assert response.status_code==410,response.content
    assert response.json()['error']['code']=='GENERIC_PRICING_RETIRED'
    response=api_client.post(endpoint,json.dumps({'policy_key':'OBSOLETE','policy_version':1,
        'scope':{'kind':'SERVICE_ZONE','key':'TEST'},'currency':'INR',
        'rules':[{'id':'BASE','kind':'BASE','rate':'1','component':'BASE',
                  'basis':'SESSION_COUNT','tax_class':'VAT'}],
        'reason':'Test unsupported generic operation'}),
        content_type='application/json',**authz)
    assert response.status_code==410,response.content
    assert not PricingPolicy.objects.filter(policy_key='OBSOLETE').exists()


def test_legacy_customer_quote_and_acceptance_rejected():
    account=Account.objects.create(email=f'customer-retired-{uuid4().hex}@example.invalid',
        password_hash='x',email_verified=True)
    principal=Principal.objects.create(account=account,plane='app',status='ACTIVE')
    authz={'HTTP_AUTHORIZATION':'Bearer '+auth.make_tokens(principal)['access_token']}
    client=Client()
    for path in ('/api/v1/app/service-requests/00000000-0000-4000-8000-000000000001/quote',
                 '/api/v1/app/quotes/00000000-0000-4000-8000-000000000001/accept'):
        response=client.post(path,content_type='application/json',**authz)
        assert response.status_code==410,response.content
        assert response.json()['error']['code']=='LEGACY_QUOTE_RETIRED'
