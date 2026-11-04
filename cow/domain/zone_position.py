"""Pure, conservative WGS84 radius membership; not a routing/billable distance.

Coordinates are validated; uncertainty near a boundary is UNKNOWN, not INSIDE.
A trusted position provider must validate location origin, freshness and accuracy.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from math import asin, cos, radians, sin, sqrt


class ZonePositionError(ValueError):
    pass


def exact(value, label, lower, upper):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ZonePositionError(f"Invalid {label}")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ZonePositionError(f"Invalid {label}") from None
    if not result.is_finite() or result < lower or result > upper:
        raise ZonePositionError(f"Out-of-range {label}")
    return result


@dataclass(frozen=True)
class VerifiedPosition:
    latitude: Decimal
    longitude: Decimal
    accuracy_m: Decimal
    observed_at: datetime
    evidence_ref: str

    def __post_init__(self):
        object.__setattr__(self, "latitude", exact(self.latitude, "latitude", -90, 90))
        object.__setattr__(self, "longitude", exact(self.longitude, "longitude", -180, 180))
        object.__setattr__(self, "accuracy_m", exact(self.accuracy_m, "accuracy_m", 0, 100000))
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ZonePositionError("Aware observed_at required")
        if not isinstance(self.evidence_ref, str) or not (1 <= len(self.evidence_ref.strip()) <= 160):
            raise ZonePositionError("Trusted evidence reference required")


def radial_relation(center_lat, center_lon, radius_m, position: VerifiedPosition, *, max_accuracy_m=100):
    """INSIDE/OUTSIDE/UNKNOWN; any GPS uncertainty crossing edge is UNKNOWN.

    Haversine is used ONLY for radial membership, never as chargeable distance
    or an ETA. For geo-policy authorization, caller must also validate sample
    provenance, age, and independent service/driver area constraints.
    """
    if not isinstance(position, VerifiedPosition):
        raise ZonePositionError("VerifiedPosition required")
    lat = exact(center_lat, "center latitude", -90, 90)
    lon = exact(center_lon, "center longitude", -180, 180)
    radius = exact(radius_m, "radius_m", 0, 100000000)
    if radius == 0:
        raise ZonePositionError("Zero-radius zone invalid")
    threshold = exact(max_accuracy_m, "max_accuracy_m", 0, 100000)
    if position.accuracy_m > threshold:
        return "UNKNOWN"
    p1, p2 = radians(float(lat)), radians(float(position.latitude))
    d1 = p2 - p1
    d2 = radians(float(position.longitude - lon))
    a = sin(d1 / 2) ** 2 + cos(p1) * cos(p2) * sin(d2 / 2) ** 2
    distance = 6371008.8 * 2 * asin(min(1.0, sqrt(max(0.0, a))))
    accuracy = float(position.accuracy_m)
    r = float(radius)
    if distance + accuracy <= r:
        return "INSIDE"
    if distance - accuracy > r:
        return "OUTSIDE"
    return "UNKNOWN"
