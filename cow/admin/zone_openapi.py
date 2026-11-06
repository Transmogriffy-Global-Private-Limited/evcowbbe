"""Source-backed OpenAPI enrichment for the exclusive Zone commercial API.

Documentation never creates an operation, changes authentication, or changes
runtime HTTP semantics. The schemas themselves live in zone_contracts.py.
"""
from __future__ import annotations

from copy import deepcopy

PREFIX = '/api/v1/admin/zones'
TAGS = {
    'zones': '17 | Admin Zones',
    'assignments': '18 | Admin Zone assignments',
    'gst': '19 | Admin Zone GST',
    'tariffs': '20 | Admin Zone tariffs',
    'preview': '21 | Admin Zone simulation',
}
DESCRIPTIONS = {
    TAGS['zones']: 'Versioned service zones. Draft, publish, retire; old versions remain historical.',
    TAGS['assignments']: 'Assign a published Zone to driver duties (roaming) or chargers (on-site QR), with effective intervals and audit.',
    TAGS['gst']: 'Channel-specific tax bindings with recorded legal/seller decision references. Coordinates never decide GST.',
    TAGS['tariffs']: 'The ONLY supported Admin tariff mode: versioned Zone composite plans, published on the existing PricingPolicy ledger.',
    TAGS['preview']: 'Admin configuration simulation only. No verified customer position, committed quote, debit or charger command.',
}
SUMMARY = {
    'adminZonesList': 'List Zone revisions',
    'adminZoneCreate': 'Create Zone draft',
    'adminZoneGet': 'Get Zone revision',
    'adminZonePublish': 'Publish Zone revision',
    'adminZoneRetire': 'Retire Zone revision',
    'adminZoneAssignmentsList': 'List Zone assignments',
    'adminZoneAssign': 'Assign duty or charger to Zone',
    'adminZoneAssignmentClose': 'Close Zone assignment',
    'adminZoneGSTList': 'List Zone GST decisions',
    'adminZoneGSTDraft': 'Draft Zone GST decision',
    'adminZoneGSTApprove': 'Approve Zone GST decision',
    'adminZoneGSTClose': 'Close Zone GST interval',
    'adminZoneTariffsList': 'List Zone composite tariffs',
    'adminZoneTariffDraft': 'Draft Zone composite tariff',
    'adminZoneTariffGet': 'Get Zone composite tariff',
    'adminZoneTariffPublish': 'Publish Zone composite tariff',
    'adminZoneTariffWithdraw': 'Withdraw Zone composite tariff',
    'adminZoneScenarioPreview': 'Simulate nonbinding Zone price',
}

EXAMPLE_UUID = '00000000-0000-4000-8000-000000000001'
EXAMPLE_ZONE_UUID = '00000000-0000-4000-8000-000000000002'
EXAMPLE_REASON = {'reason': 'Reviewed configuration and operational authorization'}


def zone_tag(path: str) -> str:
    """Called only for paths under PREFIX, ordered by specific path first."""
    if path == PREFIX + '/preview':
        return TAGS['preview']
    if (path.startswith(PREFIX + '/assignments/') or
            path.endswith('/assignments')):
        return TAGS['assignments']
    if path.startswith(PREFIX + '/gst') or path.endswith('/gst'):
        return TAGS['gst']
    if path.startswith(PREFIX + '/tariffs') or path.endswith('/tariffs'):
        return TAGS['tariffs']
    return TAGS['zones']


def _pricing_sample(channel: str) -> dict:
    qr = channel == 'ON_SITE_QR'
    transport = '0' if qr else '100.00'
    distance = '0' if qr else '5.00'
    plan = {
        'policy_id': 'KOLKATA_ZONE_TARIFF',
        'policy_version': 1,
        'zone_id': 'kolkata.core',
        'channel': channel,
        'currency': 'INR',
        'profiles': {
            'INSIDE': {
                'charging': {'basis': 'ENERGY_KWH', 'rate': '10.00', 'tax_class': 'ENERGY'},
                'transport': {'rate': transport, 'tax_class': 'MOBILITY', 'applies_on': ['UPFRONT']},
                'distance': {
                    'minimum_billable_m': 0 if qr else 2000,
                    'slabs': [{'up_to_m': None, 'rate_per_km': distance}],
                    'tax_class': 'MOBILITY', 'applies_on': ['UPFRONT'],
                },
                'extras': [],
            },
        },
    }
    return {'zone_id': EXAMPLE_ZONE_UUID, 'starts_at': '2026-11-07T09:00:00+05:30',
            'plan': plan, **EXAMPLE_REASON}


def _examples(path: str, method: str) -> dict | None:
    if method != 'post':
        return None
    if path == PREFIX:
        return {'zone': {'summary': 'Draft radial Zone', 'value': {
            'zone_key': 'kolkata.core', 'version': 1, 'name': 'Kolkata Core',
            'center_latitude': '22.5726000', 'center_longitude': '88.3639000',
            'radius_m': 5000, 'restriction_mode': 'STRICT',
            'starts_at': '2026-11-07T09:00:00+05:30', **EXAMPLE_REASON}}}
    if path == PREFIX + '/assignments/{kind}':
        return {'assignment': {'summary': 'Use kind=duty or kind=charger in path', 'value': {
            'zone_id': EXAMPLE_ZONE_UUID, 'owner_id': EXAMPLE_UUID,
            'starts_at': '2026-11-07T09:00:00+05:30',
            'authorization_ref': 'REVIEWED:OPS:001', **EXAMPLE_REASON}}}
    if path == PREFIX + '/gst':
        return {'tax': {'summary': 'Example structure ONLY; rates need approved tax/legal review', 'value': {
            'zone_id': EXAMPLE_ZONE_UUID, 'channel': 'ROAMING',
            'binding_version': 1, 'starts_at': '2026-11-07T09:00:00+05:30',
            'seller_tax_ref': 'SELLER:REVIEWED:001',
            'legal_decision_ref': 'LEGAL:REVIEWED:001',
            'tax_profiles': {'ENERGY': {'approved': True,
                                       'sgst_percent': '0', 'cgst_percent': '0', 'igst_percent': '0'},
                             'MOBILITY': {'approved': True,
                                          'sgst_percent': '0', 'cgst_percent': '0', 'igst_percent': '0'}},
            'authorization_ref': 'REVIEWED:FINANCE:001', **EXAMPLE_REASON}}}
    if path == PREFIX + '/tariffs':
        return {'roaming': {'summary': 'Roaming: transport + distance + one charging basis',
                            'value': _pricing_sample('ROAMING')},
                'on_site_qr': {'summary': 'On-site QR: no transport/distance billing',
                               'value': _pricing_sample('ON_SITE_QR')}}
    if path == PREFIX + '/preview':
        return {'roaming': {'summary': 'Admin simulation: duty-derived Zone (NOT customer evidence)',
                            'value': {'channel': 'ROAMING', 'duty_id': EXAMPLE_UUID,
                                      'latitude': '22.5726000', 'longitude': '88.3639000',
                                      'accuracy_m': '10'}},
                'on_site_qr': {'summary': 'Admin simulation: charger-derived Zone',
                               'value': {'channel': 'ON_SITE_QR', 'charger_id': EXAMPLE_UUID,
                                         'latitude': '22.5726000', 'longitude': '88.3639000',
                                         'accuracy_m': '10'}}}
    return {'reason': {'summary': 'Audited reason', 'value': deepcopy(EXAMPLE_REASON)}}


def enrich_zone_operation(path: str, method: str, operation: dict) -> None:
    """Preserve the Ninja-generated security/request/response contracts."""
    if not path.startswith(PREFIX):
        return
    name = operation.get('operationId')
    if name not in SUMMARY:
        raise ValueError(f'Unrecognized Zone operation ID: {name}')
    operation['summary'] = SUMMARY[name]
    operation['tags'] = [zone_tag(path)]
    mode = '**Commercial mode:** Zone-owned composite tariffs only. The old generic policy editor is unsupported.'
    note = '**Safety:** Admin simulation is nonbinding; never send its result to wallets, quote acceptance, or HAL.' if path == PREFIX + '/preview' else mode
    if '/gst' in path:
        note += '\n\n**Tax authority:** The approved seller/legal decision must come from an authorized review. The zero rates in examples are placeholders, NOT Indian GST guidance.'
    if '/tariffs' in path:
        note += '\n\n**Tariff:** `plan.zone_id` is the Zone key, not its UUID. `rules` in the shared publication ledger must remain empty. The plan is the sole tariff component source.'
    if '/assignments' in path:
        note += '\n\n**Assignment:** `owner_id` is a duty UUID for kind=duty, or a charger UUID for kind=charger; their effective windows must not overlap other assignments.'
    operation['description'] = '\n\n'.join(filter(None, [operation.get('description',''), note]))

    write = method == 'post' and path != PREFIX + '/preview'
    etag_required = name in {'adminZoneTariffPublish', 'adminZoneTariffWithdraw'}
    params = operation.setdefault('parameters', [])
    names = {(p.get('in'),p.get('name')) for p in params if isinstance(p,dict)}
    if write and ('header', 'Idempotency-Key') not in names:
        params.append({'name': 'Idempotency-Key', 'in': 'header', 'required': True,
                       'description': 'Unique logical mutation key, 16..128 visible ASCII characters (excluding comma); reuse only on exact retries.',
                       'schema': {'type': 'string', 'minLength': 16, 'maxLength': 128},
                       'example': 'zone-admin-operation-0001'})
    if etag_required and ('header','If-Match') not in names:
        params.append({'name': 'If-Match', 'in': 'header', 'required': True,
                       'description': 'Current quoted state_version from a GET or prior create/transition response.',
                       'schema': {'type': 'string', 'pattern': '^"[1-9][0-9]*"$'},
                       'example': '"1"'})
    for code, response in operation.get('responses',{}).items():
        if str(code).startswith('2') and name in {'adminZoneTariffDraft','adminZoneTariffGet',
                                                  'adminZoneTariffPublish','adminZoneTariffWithdraw'}:
            response.setdefault('headers',{})['ETag'] = {
                'description': 'Quoted PricingPolicy state_version for safe publish/withdraw.',
                'schema': {'type': 'string', 'pattern': '^"[1-9][0-9]*"$'}}
    examples = _examples(path, method)
    if examples:
        media = operation.get('requestBody',{}).get('content',{}).get('application/json')
        if not isinstance(media,dict):
            raise ValueError('Zone POST missing JSON request contract: '+path)
        media['examples'] = examples

    # DomainError/validation_error in cow.api always use the shared error envelope.
    from cow.common.contracts import GO_ERROR_ENVELOPE_SCHEMA
    errors = {'401': 'Missing/invalid Admin bearer token', '403': 'Admin capability denied',
              '404': 'Zone resource or assignment not found',
              '422': 'Invalid domain record or ambiguous JSON',
              '409': 'Invalid lifecycle/overlapping effective interval/authorization conflict'}
    if write:
        errors.update({'400': 'Invalid fields or If-Match syntax',
                       '415': 'Expected application/json',
                       '428': 'Missing Idempotency-Key or If-Match'})
    if etag_required:
        errors['412'] = 'Stale policy state version'
    for code, desc in errors.items():
        operation.setdefault('responses',{}).setdefault(code,{
            'description':desc,
            'content': {'application/json': {'schema': deepcopy(GO_ERROR_ENVELOPE_SCHEMA)}}})


def zone_openapi_intro() -> str:
    return '''### Tariff authority: Zone composite only

The only supported Admin tariff workflow is `/api/v1/admin/zones/...`, with
`ROAMING` (assigned driver duty) and `ON_SITE_QR` (assigned charger) channels.
A Zone tariff combines a single primary charging basis (energy kWh, time minute,
or session), transport, distance slabs, and optional fees. Its GST binding is a
separate reviewed, channel-specific legal decision. PricingPolicy remains the
single publication/version ledger; its generic `rules` array is empty for Zone
tariffs. The prior `/api/v1/admin/pricing/policies` endpoints are retired
(HTTP 410) and not included in OpenAPI. Legacy customer quote creation
and acceptance endpoints are also retired (HTTP 410), not exposed in
OpenAPI. Only customer service-request intake remains available while the
Zone-aware binding quote/dispatch/settlement workflow is developed.

Recommended Admin sequence: publish Zone; authorize a duty/charger assignment;
draft and approve channel GST; draft a composite tariff; fetch its ETag;
publish using `Idempotency-Key` and `If-Match`; run the nonbinding scenario
preview. The preview cannot charge, finalize a customer quote or command HAL.
A full customer commitment/dispatch/settlement contract is not yet implemented.
'''
