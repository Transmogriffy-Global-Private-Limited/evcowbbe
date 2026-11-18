"""Optional routing/nav/ETA provider boundary; business core never imports a vendor.

The provider is a server-configured dotted object with route(start,end,context)
returning a validated RouteEstimate. With NONE selected no network request occurs,
no ETA or road distance is invented, and no Maps API key is needed. Existing
booking charges always retain their frozen geodesic slab metric in V3.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from django.conf import settings
from django.utils.module_loading import import_string
from cow.common.errors import reject
from cow.domain.location_geometry import decimal_coordinate

@dataclass(frozen=True)
class RouteEstimate:
    provider_id: str
    provider_version: str
    distance_road_m: int
    estimated_duration_s: int
    observed_at: datetime
    route_reference: str
    navigation_url: str|None=None

    def verify(self):
        if not self.provider_id or not self.provider_version or not self.route_reference:
            reject(503,'ROUTE_PROVIDER_EVIDENCE_INVALID','Route provider identity missing')
        if (type(self.distance_road_m) is not int or self.distance_road_m<0 or
            type(self.estimated_duration_s) is not int or self.estimated_duration_s<0):
            reject(503,'ROUTE_PROVIDER_VALUE_INVALID','Provider returned impossible route metrics')
        if not isinstance(self.observed_at,datetime) or self.observed_at.tzinfo is None:
            reject(503,'ROUTE_PROVIDER_TIMESTAMP_INVALID','Provider must provide aware timestamp')
        if self.navigation_url is not None and (not isinstance(self.navigation_url,str) or
            not self.navigation_url.startswith('https://')):
            reject(503,'ROUTE_PROVIDER_NAV_URL_INVALID','Only trusted HTTPS navigation URLs allowed')


def _coords(location):
    if not isinstance(location,dict) or not {'latitude','longitude'}<=set(location):
        reject(422,'ROUTE_COORDINATES_MISSING','Coordinates required')
    return {'latitude':str(decimal_coordinate(location['latitude'],'latitude')),
            'longitude':str(decimal_coordinate(location['longitude'],'longitude'))}


def route(start,end,context):
    start=_coords(start);end=_coords(end)
    provider_path=getattr(settings,'COW_MAPS_ROUTING_PROVIDER','NONE')
    if provider_path=='NONE':
        return {'available':False,'reason':'ROUTING_PROVIDER_NOT_CONFIGURED',
            'distance_road_m':None,'estimated_duration_s':None,
            'navigation_url':None,'tariff_distance_basis':'FROZEN_GEODESIC'}
    if not isinstance(provider_path,str) or '.' not in provider_path:
        reject(503,'ROUTE_PROVIDER_INVALID','Invalid routing provider selection')
    try:
        provider=import_string(provider_path)
    except (ImportError,AttributeError,ValueError,TypeError):
        reject(503,'ROUTE_PROVIDER_UNAVAILABLE','Configured routing provider not importable')
    if not callable(getattr(provider,'route',None)):
        reject(503,'ROUTE_PROVIDER_INCOMPLETE','Provider must implement route(start,end,context)')
    # Provider implementation owns credential retrieval, timeouts and network IO;
    # never pass credentials from customers or log provider secrets.
    result=provider.route(start,end,dict(context or {}))
    if not isinstance(result,RouteEstimate):
        reject(503,'ROUTE_PROVIDER_CONTRACT_INVALID','Provider returned an invalid route')
    result.verify()
    return {'available':True,'provider_id':result.provider_id,
            'provider_version':result.provider_version,
            'distance_road_m':result.distance_road_m,
            'estimated_duration_s':result.estimated_duration_s,
            'route_reference':result.route_reference,
            'navigation_url':result.navigation_url,
            'observed_at':result.observed_at.isoformat(),
            'tariff_distance_basis':'FROZEN_GEODESIC'}
