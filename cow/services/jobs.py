"""ServiceJob owns commercial/operational lifecycle, not physical HAL identity."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from cow.errors import reject
from cow.models import (
    AuditEvent,
    CustomerAddress,
    CustomerVehicle,
    JobEvent,
    PricingPolicy,
    Quote,
    ServiceJob,
    ServiceRequest,
)
from cow.services.pricing import quote_amount

STATES = {
    "DISPATCHING": frozenset({"ASSIGNED","CANCELLED","FAILED","EXPIRED"}),
    "ASSIGNED": frozenset({"ACCEPTED","REASSIGNING","CANCELLED","FAILED"}),
    "ACCEPTED": frozenset({"EN_ROUTE","REASSIGNING","CANCELLED","FAILED"}),
    "EN_ROUTE": frozenset({"ARRIVED","REASSIGNING","FAILED"}),
    "ARRIVED": frozenset({"SERVICE_PREPARING","FAILED"}),
    "SERVICE_PREPARING": frozenset({"CHARGING","FAILED","CANCELLED"}),
    "CHARGING": frozenset({"WRAP_UP","FAILED"}),
    "WRAP_UP": frozenset({"COMPLETED","FAILED"}),
    "REASSIGNING": frozenset({"DISPATCHING","FAILED","CANCELLED"}),
    "COMPLETED": frozenset(), "CANCELLED": frozenset(), "FAILED": frozenset(), "EXPIRED": frozenset(),
}


def assert_transition(old: str, new: str):
    if new not in STATES.get(old, ()):
        reject(409, "INVALID_JOB_TRANSITION", f"Transition {old} to {new} is not allowed")


def transition_locked(job, new, action, evidence):
    assert_transition(job.state, new)
    previous = job.state
    job.state = new
    job.version += 1
    job.save(update_fields=["state", "version", "updated_at"])
    JobEvent.objects.create(job=job, action=action, from_state=previous, to_state=new,
                            evidence=evidence)


def snapshot_vehicle(v):
    # Frozen, self-reported customer facts. Never infer hardware compatibility.
    return {
        "make": v.make, "model": v.model, "variant": v.variant,
        "registration_number": v.registration_number,
        "battery_capacity_wh": v.battery_capacity_wh,
        "connector_types": v.connector_types, "supports_ac": v.supports_ac,
        "supports_dc": v.supports_dc, "max_ac_power_w": v.max_ac_power_w,
        "max_dc_power_w": v.max_dc_power_w,
        "capability_evidence": "UNVERIFIED_CUSTOMER_DATA",
    }


def snapshot_address(a):
    return {
        "address": a.address, "latitude": str(a.latitude), "longitude": str(a.longitude),
        "instructions": a.instructions,
        "line1": a.line1, "line2": a.line2, "locality": a.locality,
        "city": a.city, "state": a.state, "postal_code": a.postal_code,
        "country_code": a.country_code, "landmark": a.landmark, "floor": a.floor,
        "contact_instructions": a.contact_instructions,
        "geocode_provider": a.geocode_provider,
        "geocode_provider_place_id": a.geocode_provider_place_id,
    }


def create_request(principal, vehicle_id, address_id, energy_wh):
    if principal.plane != "app" or not (1 <= energy_wh <= 500000):
        reject(422, "REQUEST_INVALID", "Invalid charging request")
    vehicle = CustomerVehicle.objects.filter(id=vehicle_id,owner=principal,active=True).first()
    address = CustomerAddress.objects.filter(id=address_id,owner=principal,active=True).first()
    if not vehicle or not address:
        reject(404, "NOT_FOUND", "Vehicle or address not found")
    req = ServiceRequest.objects.create(customer=principal,vehicle_snapshot=snapshot_vehicle(vehicle),
                                        address_snapshot=snapshot_address(address),requested_energy_wh=energy_wh)
    return req


def create_quote(principal, request_id):
    with transaction.atomic():
        sr = ServiceRequest.objects.select_for_update().filter(id=request_id,customer=principal).first()
        if sr is None:
            reject(404,"NOT_FOUND","Request not found")
        if sr.state != "REQUESTED":
            reject(409,"REQUEST_STATE_CONFLICT","Request already quoted")
        policy = PricingPolicy.objects.filter(status="ACTIVE", effective_from__lte=timezone.now()).order_by("-effective_from", "-revision").first()
        if policy is None or (policy.effective_to and policy.effective_to <= timezone.now()):
            reject(503,"PRICING_UNAVAILABLE","No published valid pricing policy")
        fee = quote_amount(policy.base_service_paise,sr.requested_energy_wh,policy.per_kwh_paise)
        quote = Quote.objects.create(request=sr,pricing_policy=policy,total_paise=fee,
            pricing_snapshot={"policy_id":str(policy.id),"revision":policy.revision,
                              "base_service_paise":policy.base_service_paise,"per_kwh_paise":policy.per_kwh_paise,
                              "requested_energy_wh":sr.requested_energy_wh},
            expires_at=timezone.now()+timedelta(minutes=15))
        sr.state="QUOTED"
        sr.save(update_fields=["state","updated_at"])
        return quote


def accept_quote(principal, quote_id):
    with transaction.atomic():
        quote = Quote.objects.select_related("request").filter(id=quote_id,request__customer=principal).first()
        if quote is None:
            reject(404,"NOT_FOUND","Quote not found")
        sr = ServiceRequest.objects.select_for_update().get(id=quote.request_id)
        quote.refresh_from_db()
        if quote.expires_at <= timezone.now():
            reject(410,"QUOTE_EXPIRED","Quote has expired")
        if sr.state != "QUOTED" or quote.accepted_at:
            reject(409,"QUOTE_STATE_CONFLICT","Quote already accepted or request changed")
        quote.accepted_at = timezone.now()
        quote.save(update_fields=["accepted_at","updated_at"])
        sr.state="ACCEPTED"
        sr.save(update_fields=["state","updated_at"])
        job = ServiceJob.objects.create(request=sr,accepted_quote=quote)
        JobEvent.objects.create(job=job, action="QUOTE_ACCEPTED", from_state="", to_state="DISPATCHING",evidence={"quote_id":str(quote.id)})
        AuditEvent.objects.create(actor=principal,target_type="ServiceJob",target_id=job.id,action="CREATE")
        return job
