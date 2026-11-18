"""Provider-free geodesic/radius rules. Never label geodesic metres as driven road metres."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import atan2, cos, radians, sin, sqrt, ceil

EARTH_RADIUS_M = 6_371_008.8

class GeometryError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code=code


def decimal_coordinate(raw,axis):
    if isinstance(raw,bool) or raw is None:
        raise GeometryError('GEO_COORDINATE_INVALID')
    try:
        d=Decimal(str(raw))
    except (InvalidOperation,ValueError,TypeError) as exc:
        raise GeometryError('GEO_COORDINATE_INVALID') from exc
    bound=90 if axis=='latitude' else 180 if axis=='longitude' else None
    if bound is None or not d.is_finite() or d < -bound or d > bound:
        raise GeometryError('GEO_COORDINATE_OUT_OF_RANGE')
    return d


def geodesic_m(lat1,lon1,lat2,lon2):
    """Return conservative integer straight-line great-circle distance in metres."""
    a1,a2=(radians(float(decimal_coordinate(lat,'latitude'))) for lat in (lat1,lat2))
    b1,b2=(radians(float(decimal_coordinate(lon,'longitude'))) for lon in (lon1,lon2))
    da=a2-a1; db=b2-b1
    x=sin(da/2)**2+cos(a1)*cos(a2)*sin(db/2)**2
    return int(ceil(EARTH_RADIUS_M*2*atan2(sqrt(max(0,min(1,x))),sqrt(max(0,1-min(1,x))))))


@dataclass(frozen=True)
class RadiusVerdict:
    state: str  # INSIDE, OUTSIDE, UNCERTAIN
    distance_m: int
    radius_m: int
    accuracy_m: int
    method: str = 'WGS84_GREAT_CIRCLE'


def classify_radius(lat,lon,center_lat,center_lon,radius_m,accuracy_m=0):
    if type(radius_m) is not int or radius_m<=0 or radius_m>2_000_000:
        raise GeometryError('GEO_RADIUS_INVALID')
    if type(accuracy_m) is not int or not 0<=accuracy_m<=10_000:
        raise GeometryError('GEO_ACCURACY_INVALID')
    measured=geodesic_m(lat,lon,center_lat,center_lon)
    # Device-supplied accuracy is only a declared uncertainty, not provenance.
    if measured+accuracy_m<=radius_m:
        state='INSIDE'
    elif measured-accuracy_m>radius_m:
        state='OUTSIDE'
    else:
        state='UNCERTAIN'
    return RadiusVerdict(state,measured,radius_m,accuracy_m)
