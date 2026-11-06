"""ServiceJob lifecycle; customer quote binding is Zone-only and pending."""


from cow.common.errors import reject
from cow.models import (
    CustomerAddress,
    CustomerVehicle,
    JobEvent,
    ServiceJob,
    ServiceRequest,
)

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
    """The legacy non-Zone quote engine is not an authorized tariff mode."""
    reject(410, "LEGACY_QUOTE_RETIRED",
           "Customer commitment requires an assigned Zone and frozen approved tariff")


def accept_quote(principal, quote_id):
    """Fail closed until a durable Zone-owned customer commitment is implemented."""
    reject(410, "LEGACY_QUOTE_RETIRED",
           "Legacy quotes cannot be accepted; Zone commitment is not yet available")
