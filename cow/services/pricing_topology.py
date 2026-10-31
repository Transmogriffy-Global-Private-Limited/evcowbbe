"""Go pricing/temporal.go compatible active-policy scope/window resolution.

Policies are dictionaries with scope, starts_at, ends_at, terms, active.
Precedence must come from a trusted server decision, never client input.
"""
from __future__ import annotations
from datetime import datetime
from re import fullmatch
from cow.services.pricing_engine import PricingError, NoApplicablePolicy, validate

class TemporalConflict(PricingError):
    code = "PRICING_TEMPORAL_CONFLICT"


def _valid_scope(scope):
    return isinstance(scope, dict) and fullmatch(r"[A-Z][A-Z0-9_]{0,63}", str(scope.get("kind", ""))) is not None and fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", str(scope.get("key", ""))) is not None


def _window(p):
    start, end = p.get("starts_at"), p.get("ends_at")
    if (start is not None and (not isinstance(start, datetime) or start.tzinfo is None or start.utcoffset() is None)) or (end is not None and (not isinstance(end, datetime) or end.tzinfo is None or end.utcoffset() is None)) or (end is not None and (start is None or end <= start)):
        raise PricingError("Invalid temporal window")
    return start, end


def _contained(outer, inner):
    a,b=_window(outer); c,d=_window(inner)
    return a <= c and d <= b and (a < c or d < b)


def _overlap(x,y):
    a,b=_window(x); c,d=_window(y)
    return a < d and c < b


def validate_topology(policies):
    seen, grouped = set(), {}
    for p in policies:
        scope = p.get("scope")
        if not _valid_scope(scope):
            raise PricingError("Invalid policy scope")
        _window(p)
        terms = p.get("terms")
        validate(terms)
        version = terms["policy_id"], terms["policy_version"]
        if version in seen:
            raise TemporalConflict("Repeated policy version")
        seen.add(version)
        if p.get("active"):
            grouped.setdefault((scope["kind"],scope["key"]), []).append(p)
    for group in grouped.values():
        roots = [p for p in group if p.get("starts_at") is None]
        if len(roots) > 1:
            raise TemporalConflict("Duplicate root")
        open_starts = [p["starts_at"] for p in group if p.get("starts_at") is not None and p.get("ends_at") is None]
        if len(open_starts) != len(set(open_starts)):
            raise TemporalConflict("Duplicate open-ended start")
        bounded = [p for p in group if p.get("starts_at") is not None and p.get("ends_at") is not None]
        for i,x in enumerate(bounded):
            for y in bounded[i+1:]:
                if _overlap(x,y) and not (_contained(x,y) or _contained(y,x)):
                    raise TemporalConflict("Crossing or identical bounded windows")
    return True


def resolve(policies, precedence, at):
    if not isinstance(at,datetime) or at.tzinfo is None or at.utcoffset() is None or not precedence:
        raise PricingError("Trusted resolution time and precedence required")
    validate_topology(policies)
    seen = set()
    for scope in precedence:
        if not _valid_scope(scope):
            raise PricingError("Invalid precedence scope")
        key = scope["kind"], scope["key"]
        if key in seen:
            raise PricingError("Duplicate precedence scope")
        seen.add(key)
        matched = [p for p in policies if p.get("active") and (p["scope"]["kind"],p["scope"]["key"])==key and (p.get("starts_at") is None or p["starts_at"]<=at) and (p.get("ends_at") is None or at<p["ends_at"])]
        bounded = [p for p in matched if p.get("starts_at") is not None and p.get("ends_at") is not None]
        open_ended = [p for p in matched if p.get("starts_at") is not None and p.get("ends_at") is None]
        roots = [p for p in matched if p.get("starts_at") is None]
        if bounded:
            return min(bounded, key=lambda p:(p["ends_at"]-p["starts_at"],-p["starts_at"].timestamp()))
        if open_ended:
            return max(open_ended,key=lambda p:p["starts_at"])
        if roots:
            return roots[0]
    raise NoApplicablePolicy("No trusted eligible policy")
