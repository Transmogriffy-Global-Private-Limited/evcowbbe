#!/usr/bin/env python3
"""Read-only native OpenAPI acceptance: source -> Ninja -> FE decorator."""
from __future__ import annotations

import os

from scripts.dev import env_file

env_file()
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')

import django
django.setup()

from cow.api import api
from cow.admin.zone_openapi import PREFIX, SUMMARY


def main() -> int:
    schema = api.get_openapi_schema()
    paths = schema['paths']
    generic = sorted(p for p in paths if p.startswith('/api/v1/admin/pricing/policies'))
    if generic:
        raise RuntimeError(f'Legacy generic pricing is still in Swagger: {generic}')
    legacy_quotes = ('/api/v1/app/service-requests/{request_id}/quote', '/api/v1/app/quotes/{quote_id}/accept')
    if any(p in paths for p in legacy_quotes):
        raise RuntimeError('Legacy non-Zone customer quote or acceptance still present in Swagger')
    found = []
    for path, item in paths.items():
        if not path.startswith(PREFIX):
            continue
        for method, op in item.items():
            if method.lower() not in {'get','post','put','patch','delete'}:
                continue
            found.append((method.upper(),path,op['operationId']))
            if op['operationId'] not in SUMMARY:
                raise RuntimeError('Unexpected Zone OpenAPI operation: '+str(found[-1]))
            if op.get('x-fe-schema-detail') != 'declared':
                raise RuntimeError('Zone response has an untyped success schema: '+path)
            if not op.get('security'):
                raise RuntimeError('Zone operation lacks Admin bearer security: '+path)
            if method.lower() == 'post' and path != PREFIX+'/preview':
                headers={p['name'] for p in op.get('parameters',[]) if p.get('in')=='header'}
                if 'Idempotency-Key' not in headers:
                    raise RuntimeError('Missing documented idempotency header: '+path)
                if op['operationId'] in {'adminZoneTariffPublish','adminZoneTariffWithdraw'} and 'If-Match' not in headers:
                    raise RuntimeError('Missing documented If-Match header: '+path)
    found_ids=[x[2] for x in found]
    if len(found_ids) != len(SUMMARY) or set(found_ids) != set(SUMMARY) or len(set(found_ids))!=len(found_ids):
        raise RuntimeError('Zone operations incomplete or duplicated: '+str(found))
    if schema.get('x-commercial-tariff-mode') != 'zone-composite-only':
        raise RuntimeError('Exclusive tariff mode not declared')
    print(f'SWAGGER_ZONE_ONLY=PASS; ZONE_OPERATIONS={len(found_ids)}; GENERIC_PRICING_OPERATIONS=0; LEGACY_QUOTE_OPERATIONS=0')
    return 0

if __name__=='__main__': raise SystemExit(main())
