"""Strict domain-separated snapshot identifiers shared by CoW charging boundaries.

No payment, user assertions, rounding, OTP or location authority lives here.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

class ChargingCommercialError(ValueError):
    def __init__(self, code):
        self.code=code
        super().__init__(code)


def canonical_id(raw):
    try:
        parsed=UUID(str(raw))
    except (TypeError,ValueError,AttributeError) as exc:
        raise ChargingCommercialError('CHARGING_ID_INVALID') from exc
    if parsed.int==0 or str(parsed)!=str(raw):
        raise ChargingCommercialError('CHARGING_ID_NOT_CANONICAL')
    return str(parsed)


def aware_utc(instant):
    if not isinstance(instant,datetime) or instant.tzinfo is None or instant.utcoffset() is None:
        raise ChargingCommercialError('CHARGING_TIMESTAMP_NOT_AWARE')
    return instant.astimezone(timezone.utc)


def stable_digest(snapshot):
    if not isinstance(snapshot,dict):
        raise ChargingCommercialError('CHARGING_SNAPSHOT_NOT_OBJECT')
    try:
        raw=json.dumps(snapshot,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
    except (ValueError,TypeError,OverflowError) as exc:
        raise ChargingCommercialError('CHARGING_SNAPSHOT_NOT_JSON') from exc
    return hashlib.sha256(raw).hexdigest()
