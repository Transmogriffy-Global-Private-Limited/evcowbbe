"""Guard financial policy JSON before framework parsing loses duplicate keys."""
import json

LIMIT = 32768  # Go API's global MaxBytesReader; pricing-specific maximum is higher.

class InvalidPricingBody(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidPricingBody("Duplicate financial JSON key")
        result[key] = value
    return result


def reject_constant(value):
    raise InvalidPricingBody("Non-finite financial JSON value")


def check_body(raw):
    if not isinstance(raw, bytes) or not 2 <= len(raw) <= LIMIT:
        raise InvalidPricingBody("Financial JSON body size is invalid")
    try:
        result=json.loads(raw.decode("utf-8"),object_pairs_hook=unique_object,
                          parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise InvalidPricingBody("Invalid financial JSON structure") from exc
    if not isinstance(result,dict):
        raise InvalidPricingBody("Financial JSON body must be an object")
    return result
