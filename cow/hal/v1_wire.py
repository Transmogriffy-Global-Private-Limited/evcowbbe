"""Go ocpp-hal-go-new v1 transport, never a frontend or business-authority API.

One configured endpoint, one CMS service identity, no redirects, retries, DB I/O,
or arbitrary paths. External commands and reads are requested only by the
caller that already owns the durable authorization and request identity.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx


class WireError(RuntimeError):
    def __init__(self, code: str, *, status: int | None = None):
        super().__init__(code)
        self.code, self.status = code, status


def uid(raw) -> str:
    try:
        parsed = UUID(str(raw))
    except (ValueError, TypeError, AttributeError) as exc:
        raise WireError('INVALID_UUID') from exc
    if parsed.int == 0 or str(parsed) != str(raw):
        raise WireError('NONCANONICAL_UUID')
    return str(parsed)


def endpoint(raw: str) -> str:
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        raise WireError('HAL_BASE_URL_INVALID')
    try:
        value = urlsplit(raw)
        host = value.hostname
        port = value.port
    except ValueError as exc:
        raise WireError('HAL_BASE_URL_INVALID') from exc
    if (value.scheme not in {'http', 'https'} or not host or value.username or
            value.password or value.path not in {'', '/'} or value.query or
            value.fragment or port == 0):
        raise WireError('HAL_BASE_URL_INVALID')
    # Plain HTTP is only permissible on a strictly local trusted endpoint.
    if value.scheme == 'http' and host not in {'localhost', '127.0.0.1', '::1'}:
        raise WireError('HAL_TLS_REQUIRED')
    return raw.rstrip('/')


def _uuid_field(value: dict, field: str, expected: str | None = None):
    actual = uid(value.get(field))
    if expected and actual != uid(expected):
        raise WireError('HAL_IDENTITY_MISMATCH')
    return actual


def _bound_uuid_field(value: dict, field: str, expected):
    if expected is None:
        raise WireError('HAL_SCOPE_EXPECTATION_REQUIRED')
    return _uuid_field(value, field, uid(expected))


def _mapping_response(body: dict, expected: dict):
    """Do not mark a CoW mapping synchronized unless Go echoes its exact scope.

    Connector order is immaterial; connector ownership and numbers are not.
    The Go v1 mapping view contains the full immutable mapping identifiers.
    """
    if not isinstance(expected, dict) or not isinstance(body, dict) or not isinstance(body.get('mapping'), dict):
        raise WireError('HAL_MAPPING_RESPONSE_INVALID')
    actual = body['mapping']
    _bound_uuid_field(actual, 'cpo_id', expected.get('cpo_id'))
    _bound_uuid_field(actual, 'cms_charger_id', expected.get('cms_charger_id'))
    if (not isinstance(expected.get('charger_ocpp_identity'), str) or
            actual.get('charger_ocpp_identity') != expected['charger_ocpp_identity']):
        raise WireError('HAL_MAPPING_IDENTITY_MISMATCH')
    if actual.get('expected_serial', '') != expected.get('expected_serial', ''):
        raise WireError('HAL_MAPPING_SERIAL_MISMATCH')
    if (type(actual.get('enabled')) is not bool or
            actual['enabled'] is not expected.get('enabled')):
        raise WireError('HAL_MAPPING_ENABLED_MISMATCH')
    expected_connectors = expected.get('connectors')
    actual_connectors = actual.get('connectors')
    if (not isinstance(expected_connectors, list) or not expected_connectors or
            not isinstance(actual_connectors, list) or
            len(actual_connectors) != len(expected_connectors)):
        raise WireError('HAL_MAPPING_CONNECTORS_MISMATCH')

    def _connectors(entries):
        out = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise WireError('HAL_MAPPING_CONNECTORS_MISMATCH')
            connector_id = _uuid_field(entry, 'cms_connector_id')
            number = entry.get('ocpp_connector_number')
            if type(number) is not int or number <= 0:
                raise WireError('HAL_MAPPING_CONNECTORS_MISMATCH')
            # Go's connector-mapping view includes these exact fields.
            _bound_uuid_field(entry, 'cpo_id', expected.get('cpo_id'))
            _bound_uuid_field(entry, 'cms_charger_id', expected.get('cms_charger_id'))
            if entry.get('charger_ocpp_identity') != expected['charger_ocpp_identity']:
                raise WireError('HAL_MAPPING_CONNECTORS_MISMATCH')
            out.add((connector_id, number))
        if len(out) != len(entries):
            raise WireError('HAL_MAPPING_CONNECTORS_MISMATCH')
        return out

    # The outbound CMS connector snapshot carries only connector ID and number.
    # The Go echo additionally carries CPO/charger/OCPP identities; validate
    # those on the provider side without requiring them in the CMS request.
    expected_pairs = set()
    for entry in expected_connectors:
        if not isinstance(entry, dict):
            raise WireError('HAL_MAPPING_CONNECTORS_MISMATCH')
        connector_id = _bound_uuid_field(entry, 'cms_connector_id', entry.get('cms_connector_id'))
        number = entry.get('ocpp_connector_number')
        if type(number) is not int or number <= 0:
            raise WireError('HAL_MAPPING_CONNECTORS_MISMATCH')
        expected_pairs.add((connector_id, number))
    if len(expected_pairs) != len(expected_connectors) or _connectors(actual_connectors) != expected_pairs:
        raise WireError('HAL_MAPPING_CONNECTORS_MISMATCH')
    return body


def _command_response(body: dict, expected: str, kind: str | None = None):
    if not isinstance(body, dict) or not isinstance(body.get('command'), dict):
        raise WireError('HAL_COMMAND_RESPONSE_INVALID')
    command = body['command']
    _uuid_field(command, 'cms_command_id', expected)
    _uuid_field(command, 'hal_command_id')
    if command.get('kind') not in {'START', 'STOP'}:
        raise WireError('HAL_COMMAND_KIND_INVALID')
    if kind and command['kind'] != kind:
        raise WireError('HAL_COMMAND_KIND_MISMATCH')
    if not isinstance(command.get('state'), str) or not command['state']:
        raise WireError('HAL_COMMAND_STATE_INVALID')
    if not isinstance(command.get('updated_at'), str) or not command['updated_at']:
        raise WireError('HAL_COMMAND_TIME_MISSING')
    if command.get('hal_transaction_id') is not None:
        _uuid_field(command, 'hal_transaction_id')
    return body


def _transaction_response(body: dict, *, intent: str | None = None,
                          transaction: str | None = None):
    if not isinstance(body, dict) or not isinstance(body.get('transaction'), dict):
        raise WireError('HAL_TRANSACTION_RESPONSE_INVALID')
    value = body['transaction']
    _uuid_field(value, 'hal_transaction_id', transaction)
    _uuid_field(value, 'cms_start_intent_id', intent)
    _uuid_field(value, 'cms_charger_id')
    _uuid_field(value, 'cms_connector_id')
    if type(value.get('ocpp_transaction_id')) is not int or value['ocpp_transaction_id'] <= 0:
        raise WireError('HAL_OCPP_TRANSACTION_INVALID')
    return body


def _operation_response(body: dict, expected: dict):
    """Bind a Go receipt/lookup to the *durably frozen* CoW command scope.

    Go's public operation view has CPO, charger, connector UUID/number and kind,
    but deliberately not the OCPP identity. That identity is bound through the
    separately validated mapping and original provider admission, not guessed.
    """
    if not isinstance(expected, dict) or not isinstance(body, dict) or not isinstance(body.get('operation'), dict):
        raise WireError('HAL_OPERATION_RESPONSE_INVALID')
    value = body['operation']
    _uuid_field(value, 'hal_operation_id')
    _bound_uuid_field(value, 'cms_operation_id', expected.get('cms_operation_id'))
    _bound_uuid_field(value, 'cpo_id', expected.get('cpo_id'))
    _bound_uuid_field(value, 'cms_charger_id', expected.get('cms_charger_id'))
    connector_id = expected.get('cms_connector_id') or ''
    if connector_id:
        _bound_uuid_field(value, 'cms_connector_id', connector_id)
    elif value.get('cms_connector_id', '') not in ('', None):
        raise WireError('HAL_OPERATION_CONNECTOR_MISMATCH')
    number = value.get('ocpp_connector_number')
    if (type(number) is not int or type(expected.get('ocpp_connector_number')) is not int or
            number != expected['ocpp_connector_number']):
        raise WireError('HAL_OPERATION_CONNECTOR_MISMATCH')
    if not isinstance(expected.get('kind'), str) or value.get('kind') != expected['kind']:
        raise WireError('HAL_OPERATION_KIND_MISMATCH')
    if (not isinstance(value.get('state'), str) or not value['state'] or
            not isinstance(value.get('updated_at'), str) or not value['updated_at']):
        raise WireError('HAL_OPERATION_STATE_INVALID')
    return body


@dataclass(frozen=True)
class V1Client:
    base_url: str
    bearer_token: str
    timeout_seconds: float = 8.0
    transport: httpx.BaseTransport | None = None

    def __post_init__(self):
        object.__setattr__(self, 'base_url', endpoint(self.base_url))
        if (not isinstance(self.bearer_token, str) or
                len(self.bearer_token) < 32 or
                any(c in self.bearer_token for c in '\r\n\x00')):
            raise WireError('HAL_SERVICE_TOKEN_INVALID')
        if not 0.5 <= self.timeout_seconds <= 30:
            raise WireError('HAL_TIMEOUT_INVALID')

    def _call(self, method: str, path: str, *, body: dict | None = None,
              idem: str | None = None, correlation: str | None = None,
              codes: tuple[int, ...] = (200,)) -> dict:
        if method not in {'GET', 'PUT', 'POST'} or not path.startswith('/v1/'):
            raise WireError('HAL_PATH_INVALID')
        headers = {'Authorization': 'Bearer ' + self.bearer_token,
                   'Accept': 'application/json'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if idem is not None:
            headers['Idempotency-Key'] = uid(idem)
        if correlation is not None:
            headers['X-Correlation-ID'] = uid(correlation)
        try:
            with httpx.Client(timeout=self.timeout_seconds,
                              follow_redirects=False, transport=self.transport,
                              trust_env=False) as client:
                response = client.request(method, self.base_url + path,
                                          json=body, headers=headers)
        except httpx.RequestError as exc:
            raise WireError('HAL_OUTCOME_UNKNOWN') from exc
        if response.status_code not in codes:
            # Do not surface provider body; 3xx is always rejected.
            raise WireError('HAL_HTTP_FAILURE', status=response.status_code)
        try:
            result = response.json() if response.content else {}
        except ValueError as exc:
            raise WireError('HAL_RESPONSE_NOT_JSON') from exc
        if not isinstance(result, dict):
            raise WireError('HAL_RESPONSE_INVALID')
        return result

    def sync_mapping(self, charger_id: str, mapping: dict, correlation: str) -> dict:
        charger_id = uid(charger_id)
        if mapping.get('cms_charger_id') != charger_id:
            raise WireError('HAL_MAPPING_IDENTITY_MISMATCH')
        result = self._call('PUT', '/v1/mappings/chargers/' + charger_id,
                            body=mapping, idem=charger_id, correlation=correlation,
                            codes=(200, 201))
        return _mapping_response(result, mapping)

    def start(self, payload: dict, correlation: str) -> dict:
        cmd = uid(payload.get('cms_command_id'))
        result = self._call('POST', '/v1/remote-commands/start', body=payload,
                            idem=cmd, correlation=correlation, codes=(202,))
        return _command_response(result, cmd, 'START')

    def stop(self, payload: dict, correlation: str) -> dict:
        cmd = uid(payload.get('cms_command_id'))
        result = self._call('POST', '/v1/remote-commands/stop', body=payload,
                            idem=cmd, correlation=correlation, codes=(202,))
        return _command_response(result, cmd, 'STOP')

    def command(self, command_id: str) -> dict:
        command_id = uid(command_id)
        return _command_response(self._call('GET', '/v1/remote-commands?cms_command_id='+command_id), command_id)

    def transaction_for_intent(self, intent_id: str) -> dict:
        intent_id = uid(intent_id)
        return _transaction_response(self._call('GET', '/v1/transactions?cms_start_intent_id='+intent_id), intent=intent_id)

    def transaction(self, hal_transaction_id: str) -> dict:
        hal_transaction_id = uid(hal_transaction_id)
        return _transaction_response(self._call('GET', '/v1/transactions/'+hal_transaction_id), transaction=hal_transaction_id)

    def charger_runtime(self, identity: str) -> dict:
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,150}', identity):
            raise WireError('HAL_CHARGER_IDENTITY_INVALID')
        return self._call('GET', '/v1/runtime/chargers/' + quote(identity, safe=''))

    def connector_runtime(self, connector_id: str) -> dict:
        return self._call('GET', '/v1/runtime/connectors/'+uid(connector_id))

    def charger_operation(self, payload: dict, correlation: str) -> dict:
        op = uid(payload.get('cms_operation_id'))
        response = self._call('POST','/v1/charger-operations',body=payload,idem=op,
                              correlation=correlation,codes=(202,))
        return _operation_response(response, payload)

    def operation(self, operation_id: str, expected: dict) -> dict:
        """A lookup without the original operation snapshot is not trustworthy."""
        operation_id = uid(operation_id)
        if not isinstance(expected, dict) or expected.get('cms_operation_id') != operation_id:
            raise WireError('HAL_SCOPE_EXPECTATION_REQUIRED')
        response = self._call('GET', '/v1/charger-operations?cms_operation_id='+operation_id)
        return _operation_response(response, expected)

    def configuration(self, scope_id: str, charger_id: str, identity: str,
                      keys: list[str]) -> dict:
        uid(scope_id); uid(charger_id)
        if len(keys)>64 or not all(re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',x) for x in keys):
            raise WireError('HAL_CONFIGURATION_KEYS_INVALID')
        return self._call('POST','/v1/charger-configurations/read',body={
            'cpo_id':scope_id,'cms_charger_id':charger_id,
            'charger_ocpp_identity':identity,'keys':keys})

    def requeue_fact(self, fact_id: str, correlation: str) -> dict:
        fact_id = uid(fact_id)
        result = self._call('POST', '/v1/facts/'+fact_id+'/requeue',
                            correlation=correlation,codes=(202,))
        if uid(result.get('fact_id'))!=fact_id or result.get('status')!='PENDING':
            raise WireError('HAL_FACT_REQUEUE_INVALID')
        return result
