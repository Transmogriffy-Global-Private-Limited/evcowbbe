"""Self-tests for the parity checker; these use no Django database or listener."""

import unittest

from scripts.check_go_wire_contract import (
    _canonical_schema,
    _current_classification,
    compare_documents,
)


def document(operation, *, security_schemes=None):
    return {
        "components": {"schemas": {}, "securitySchemes": security_schemes or {}},
        "paths": {"/example": {"post": operation}},
    }


def operation(*, request=None, responses=None, parameters=None, security=None):
    result = {"responses": responses or {"200": {"description": "OK"}}}
    if request is not None:
        result["requestBody"] = {"required": True, "content": {"application/json": {"schema": request}}}
    if parameters is not None:
        result["parameters"] = parameters
    if security is not None:
        result["security"] = security
    return result


class GoWireCheckerTests(unittest.TestCase):
    def test_catches_nested_array_constraint_difference(self):
        go = document(operation(request={"type": "array", "minItems": 1, "items": {"type": "string"}}))
        python = document(operation(request={"type": "array", "items": {"type": "string"}}))
        findings = compare_documents(go, python)["discrepancies"]
        self.assertEqual(findings[0]["type"], "request_schema_mismatch")

    def test_catches_parameter_requiredness_and_error_status(self):
        parameter = {"name": "If-Match", "in": "header", "required": True, "schema": {"type": "string"}}
        response = {
            "description": "Error",
            "content": {"application/json": {"schema": {"type": "object", "required": ["error"]}}},
        }
        go = document(operation(parameters=[parameter], responses={"200": {"description": "OK"}, "412": response}))
        python = document(operation(parameters=[{**parameter, "required": False}]))
        kinds = {finding["type"] for finding in compare_documents(go, python)["discrepancies"]}
        self.assertIn("parameter_mismatch", kinds)
        self.assertIn("error_response_mismatch", kinds)

    def test_preserves_reference_siblings_and_nullable_equivalence(self):
        definitions = {"Code": {"type": "string", "minLength": 6}}
        self.assertEqual(
            _canonical_schema({"$ref": "#/components/schemas/Code", "maxLength": 8}, definitions),
            {"type": "string", "minLength": 6, "maxLength": 8},
        )
        self.assertEqual(
            _canonical_schema({"anyOf": [{"type": "string"}, {"type": "null"}]}, definitions),
            {"type": ["null", "string"]},
        )

    def test_catches_security_difference(self):
        go = document(
            operation(security=[{"BearerAuth": []}]),
            security_schemes={"BearerAuth": {"type": "http", "scheme": "bearer"}},
        )
        python = document(
            operation(security=[{"PlaneBearer": []}]),
            security_schemes={"PlaneBearer": {"type": "apiKey", "in": "header", "name": "Authorization"}},
        )
        kinds = {finding["type"] for finding in compare_documents(go, python)["discrepancies"]}
        self.assertIn("security_declaration_mismatch", kinds)

    def test_treats_equivalent_security_scheme_names_as_equal(self):
        go = document(
            operation(security=[{"BearerAuth": []}]),
            security_schemes={"BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "opaque"}},
        )
        python = document(
            operation(security=[{"PlaneBearer": []}]),
            security_schemes={"PlaneBearer": {"type": "http", "scheme": "bearer", "bearerFormat": "opaque"}},
        )
        kinds = {finding["type"] for finding in compare_documents(go, python)["discrepancies"]}
        self.assertNotIn("security_declaration_mismatch", kinds)

    def test_resolves_reusable_response_header(self):
        go = document(
            operation(responses={"200": {"headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestID"}}}})
        )
        go["components"]["headers"] = {
            "RequestID": {"schema": {"type": "string", "format": "uuid"}}
        }
        python = document(
            operation(responses={"200": {"headers": {"X-Request-ID": {"schema": {"type": "string", "format": "uuid"}}}}})
        )
        kinds = {finding["type"] for finding in compare_documents(go, python)["discrepancies"]}
        self.assertNotIn("success_response_schema_mismatch", kinds)

    def test_pinned_runtime_header_normalizes_a_stale_go_declaration(self):
        go = document(operation(responses={"404": {"description": "Not found"}}))
        python = document(
            operation(
                responses={
                    "404": {
                        "headers": {
                            "X-Request-ID": {
                                "schema": {"type": "string", "format": "uuid"}
                            }
                        }
                    }
                }
            )
        )
        findings = compare_documents(go, python, pinned_go_runtime=True)["discrepancies"]
        self.assertFalse(findings)

    def test_resolves_reusable_parameter(self):
        parameter = {
            "name": "If-Match",
            "in": "header",
            "required": True,
            "schema": {"type": "string"},
        }
        go = document(operation(parameters=[{"$ref": "#/components/parameters/IfMatch"}]))
        go["components"]["parameters"] = {"IfMatch": parameter}
        python = document(operation(parameters=[parameter]))
        findings = compare_documents(go, python)["discrepancies"]
        self.assertFalse(findings)

    def test_treats_optional_query_nullable_annotation_as_wire_equivalent(self):
        parameter = {
            "name": "after",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "format": "uuid"},
        }
        python_parameter = {
            **parameter,
            "schema": {"anyOf": [parameter["schema"], {"type": "null"}]},
        }
        findings = compare_documents(
            document(operation(parameters=[parameter])),
            document(operation(parameters=[python_parameter])),
        )["discrepancies"]
        self.assertFalse(findings)

    def test_classifies_ignored_logout_body_against_pinned_handler_behavior(self):
        classification, _ = _current_classification(
            {
                "operation": "POST /api/v1/app/auth/logout",
                "type": "request_schema_mismatch",
            }
        )
        self.assertEqual(classification, "Stale Go specification")


if __name__ == "__main__":
    unittest.main()
