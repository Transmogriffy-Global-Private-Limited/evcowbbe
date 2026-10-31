"""Exact integer-paise evaluator ported from Go internal/pricing/engine.go.

Policy selection and evidence provenance belong to the trusted caller. Not a
standalone authorization to charge a customer or infer tax decisions.
"""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation, localcontext
import re

DECIMAL = re.compile(r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?$")
IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
MAX_MINOR = (1 << 63) - 1

class PricingError(ValueError):
    code = "INVALID_POLICY"

class MissingEvidence(PricingError):
    code = "MISSING_EVIDENCE"

class TaxUnavailable(PricingError):
    code = "TAX_UNAVAILABLE"

class NoApplicablePolicy(PricingError):
    code = "NO_APPLICABLE_POLICY"

class AmountOverflow(PricingError):
    code = "AMOUNT_OVERFLOW"


def _number(raw):
    if not isinstance(raw, str) or not DECIMAL.fullmatch(raw):
        raise PricingError("Expected nonnegative exact decimal string")
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise PricingError("Invalid decimal") from exc


def _minor(value):
    if value < 0:
        raise PricingError("Negative monetary amount")
    amount = int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if amount > MAX_MINOR:
        raise AmountOverflow("Int64 monetary range exceeded")
    return amount


def _checked(a, b):
    result = a + b
    if not -MAX_MINOR - 1 <= result <= MAX_MINOR:
        raise AmountOverflow("Int64 monetary range exceeded")
    return result


def _identifier(raw):
    return isinstance(raw, str) and IDENTIFIER.fullmatch(raw) is not None


def validate(terms):
    if not isinstance(terms, dict) or not terms.get("policy_id") or not isinstance(terms.get("policy_version"), int) or isinstance(terms.get("policy_version"), bool) or terms["policy_version"] < 1 or terms.get("currency") != "INR":
        raise PricingError("Invalid policy identity")
    rules = terms.get("rules")
    if not isinstance(rules, list) or not 1 <= len(rules) <= 128:
        raise PricingError("Invalid number of rules")
    ids, base, components, overrides = set(), set(), set(), set()
    for rule in rules:
        if not isinstance(rule, dict) or not _identifier(rule.get("id")) or rule["id"] in ids:
            raise PricingError("Invalid or duplicate rule ID")
        ids.add(rule["id"])
        priority = rule.get("priority", 0)
        if type(priority) is not int or not 0 <= priority <= 100_000:
            raise PricingError("Invalid priority")
        _number(rule.get("rate"))
        conditions = rule.get("conditions") or {}
        if not isinstance(conditions, dict) or any(not _identifier(k) or not _identifier(v) for k, v in conditions.items()):
            raise PricingError("Unrecognized conditional dimension")
        kind = rule.get("kind")
        if kind == "BASE":
            component, basis, tax = (rule.get(x) for x in ("component", "basis", "tax_class"))
            if not all(_identifier(x) for x in (component, basis, tax)) or rule.get("target"):
                raise PricingError("Invalid base rule")
            if component in components:
                raise PricingError("Duplicate base component")
            components.add(component)
            base.add(rule["id"])
        elif kind in ("OVERRIDE_RATE", "MULTIPLIER", "DISCOUNT_PERCENT"):
            target = rule.get("target")
            if not _identifier(target) or any(rule.get(x) for x in ("component", "basis", "tax_class")):
                raise PricingError("Invalid modifier")
            if kind == "OVERRIDE_RATE":
                if target in overrides:
                    raise PricingError("Duplicate override for one base")
                overrides.add(target)
            if kind == "DISCOUNT_PERCENT" and _number(rule["rate"]) > 100:
                raise PricingError("Discount exceeds 100 percent")
        else:
            raise PricingError("Unknown rule kind")
    if not base or any(r["target"] not in base for r in rules if r["kind"] != "BASE"):
        raise PricingError("Missing base or invalid modifier target")
    return True


def _evaluate(terms, evidence, tax_profiles, mode="PREVIEW"):
    """Return an immutable-data-shaped breakdown. Inputs must be trusted snapshots.

    All rates are decimal rupees. Each base is rounded to paise *once*, then
    modifiers and taxes are rounded in integer paise as in Go.
    """
    validate(terms)
    if mode not in {"PREVIEW", "FINAL"}:
        raise PricingError("Invalid mode")
    measurements = evidence.get("measurements", {})
    conditions = evidence.get("conditions", {})
    for r in terms["rules"]:
        for key in r.get("conditions", {}):
            if key not in conditions or not _identifier(conditions[key]):
                raise MissingEvidence("Condition is not established: " + key)
    result = dict(policy_id=terms["policy_id"], policy_version=terms["policy_version"], currency="INR", mode=mode, estimated=False, lines=[], subtotal_minor=0, tax_minor=0, total_minor=0)
    rules = terms["rules"]
    base_rules = sorted((r for r in rules if r["kind"] == "BASE"), key=lambda r: (r.get("priority", 0), r["id"]))
    taxable = {}
    for base in base_rules:
        if any(conditions[k] != v for k, v in base.get("conditions", {}).items()):
            continue
        measured = measurements.get(base["basis"])
        if not isinstance(measured, dict) or not str(measured.get("source", "")).strip():
            raise MissingEvidence("Missing measurement: " + base["basis"])
        if mode == "FINAL" and measured.get("estimate"):
            raise MissingEvidence("Finalization cannot use estimated measurements")
        quantity = _number(measured.get("quantity"))
        applicable = sorted((r for r in rules if r["kind"] != "BASE" and r["target"] == base["id"] and all(conditions[k] == v for k, v in r.get("conditions", {}).items())), key=lambda r: (r.get("priority", 0), r["id"]))
        rate = _number(base["rate"])
        for modifier in applicable:
            if modifier["kind"] == "OVERRIDE_RATE":
                rate = _number(modifier["rate"])
        price = _minor(rate * quantity * 100)
        estimated = bool(measured.get("estimate", False))
        result["estimated"] |= estimated
        result["lines"].append(dict(rule_id=base["id"], component=base["component"], kind="BASE", basis=base["basis"], quantity=measured["quantity"], rate=f"{rate:.6f}", amount_minor=price, tax_class=base["tax_class"], source=measured["source"], estimate=estimated))
        current = price
        for modifier in applicable:
            kind = modifier["kind"]
            if kind == "OVERRIDE_RATE":
                continue
            factor = _number(modifier["rate"])
            amount = _minor(Decimal(current) * factor / 100)
            if kind == "MULTIPLIER":
                delta = amount - current
            else:
                delta = -amount
            current = _checked(current, delta)
            if current < 0:
                raise PricingError("Negative result")
            result["lines"].append(dict(rule_id=modifier["id"], component=base["component"], kind=kind, rate=modifier["rate"], amount_minor=delta, tax_class=base["tax_class"], target_rule_id=base["id"]))
        result["subtotal_minor"] = _checked(result["subtotal_minor"], current)
        taxable[base["tax_class"]] = _checked(taxable.get(base["tax_class"], 0), current)
    if not result["lines"]:
        raise NoApplicablePolicy("No applicable pricing rule")
    for tax_class in sorted(taxable):
        profile = (tax_profiles or {}).get(tax_class)
        if not isinstance(profile, dict) or profile.get("approved") is not True:
            raise TaxUnavailable("No approved tax profile for " + tax_class)
        try:
            rates = [_number(profile[key]) for key in ("sgst_percent", "cgst_percent", "igst_percent")]
        except (KeyError, PricingError) as exc:
            raise TaxUnavailable("Invalid tax profile") from exc
        if any(x > 100 for x in rates) or (rates[2] and (rates[0] or rates[1])):
            raise TaxUnavailable("Conflicting tax treatment")
        for name, rate in zip(("SGST", "CGST", "IGST"), rates):
            if rate == 0:
                continue
            amount = _minor(Decimal(taxable[tax_class]) * rate / 100)
            result["tax_minor"] = _checked(result["tax_minor"], amount)
            result["lines"].append(dict(rule_id=f"TAX_{tax_class}_{name}", component=name, kind="TAX", rate=profile[name.lower() + "_percent"], amount_minor=amount, tax_class=tax_class))
    result["total_minor"] = _checked(result["subtotal_minor"], result["tax_minor"])
    if _checked(sum(line["amount_minor"] for line in result["lines"]), 0) != result["total_minor"]:
        raise PricingError("Line sum differs from amount")
    return result


def evaluate(terms, evidence, tax_profiles, mode="PREVIEW"):
    # Go uses exact big.Rat. Context 80 exceeds the maximum possible input
    # precision (two <=12+6 decimals and a 100x paise conversion).
    with localcontext() as context:
        context.prec = 80
        return _evaluate(terms, evidence, tax_profiles, mode)
