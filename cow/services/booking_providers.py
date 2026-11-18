"""Explicit trust-provider ports for CoW booking and location+OTP handover.

No permissive default adapter exists. These methods must be implemented by
an independently verified, persisted application proof subsystem and selected
by server-side configuration. Never instantiate proof dataclasses from JSON.
"""
from django.conf import settings
from django.utils.module_loading import import_string
from cow.common.errors import reject


def trusted_provider():
    dotted=getattr(settings,'COW_BOOKING_TRUST_PROVIDER','')
    if type(dotted) is not str or not dotted or dotted.startswith('.'):
        reject(503,'BOOKING_TRUST_PROVIDER_UNCONFIGURED',
               'Trusted booking and handover authority unavailable')
    try:
        provider=import_string(dotted)
    except (ImportError,AttributeError,TypeError,ValueError):
        reject(503,'BOOKING_TRUST_PROVIDER_UNAVAILABLE',
               'Trusted booking and handover authority unavailable')
    for name in ('resolve_acceptance','verify_acceptance',
                 'resolve_handover','verify_handover','verify_driver_completion'):
        if not callable(getattr(provider,name,None)):
            reject(503,'BOOKING_TRUST_PROVIDER_INCOMPLETE',
                   'Configured evidence provider lacks mandatory verification functions')
    return provider
