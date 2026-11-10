"""Read-only, bounded Admin projections over existing identity/domain tables.

No customer/driver state mutation and no duplicate account directory storage.
The public resource ID is the plane-specific Principal UUID; profile_id is
reported separately because registration does not guarantee a profile row.
"""
from __future__ import annotations

from uuid import UUID

from cow.common.errors import reject
from cow.models import CustomerAddress, CustomerProfile, CustomerVehicle, DriverProfile, Principal, ServiceJob
from cow.services import rbac

_LIMIT_MAX = 100


def _page_limit(limit: int) -> int:
    if not 1 <= limit <= _LIMIT_MAX:
        reject(422, "INVALID_PAGE_LIMIT", "Page limit must be 1..100")
    return limit


def _base(plane: str, *, status: str | None = None, email: str | None = None,
          before: UUID | None = None):
    if status and status not in {"ACTIVE", "PENDING", "SUSPENDED"}:
        reject(422, "INVALID_STATUS", "Unknown principal status")
    qs = Principal.objects.filter(plane=plane).select_related("account")
    if status:
        qs = qs.filter(status=status)
    if email:
        if len(email) > 254:
            reject(422, "INVALID_EMAIL_FILTER", "Email filter too long")
        # Exact indexed lookup, never an unbounded substring directory scan.
        qs = qs.filter(account__email=email.strip().lower())
    if before:
        qs = qs.filter(id__lt=before)
    return qs.order_by("-id")


def _customer(obj: Principal, profile: CustomerProfile | None):
    return {
        "customer_id": str(obj.pk), "account_id": str(obj.account_id),
        "profile_id": str(profile.pk) if profile else None,
        "email": obj.account.email, "email_verified": obj.account.email_verified,
        "status": obj.status, "full_name": profile.full_name if profile else "",
        "created_at": obj.created_at.isoformat(),
    }


def _driver(obj: Principal, profile: DriverProfile | None):
    return {
        "driver_id": str(obj.pk), "account_id": str(obj.account_id),
        "profile_id": str(profile.pk) if profile else None,
        "email": obj.account.email, "email_verified": obj.account.email_verified,
        "status": obj.status,
        "eligibility": profile.eligibility if profile else "NO_PROFILE",
        "licence_valid_until": profile.licence_valid_until.isoformat() if profile and profile.licence_valid_until else None,
        "created_at": obj.created_at.isoformat(),
    }


def _profiles(model, principal_ids):
    return {obj.principal_id: obj for obj in model.objects.filter(principal_id__in=principal_ids)}


def list_customers(actor, *, limit=50, before=None, status=None, email=None):
    rbac.require(actor, "users.read")
    batch = list(_base("app", status=status, email=email, before=before)[:_page_limit(limit)+1])
    more = len(batch) > limit
    batch = batch[:limit]
    profiles = _profiles(CustomerProfile, [p.pk for p in batch])
    return {"items": [_customer(p, profiles.get(p.pk)) for p in batch],
            "next_cursor": str(batch[-1].pk) if more and batch else None}


def customer_detail(actor, customer_id):
    rbac.require(actor, "users.read")
    principal = Principal.objects.filter(pk=customer_id, plane="app").select_related("account").first()
    if principal is None:
        reject(404, "CUSTOMER_NOT_FOUND", "Customer not found")
    profile = CustomerProfile.objects.filter(principal=principal).first()
    result = _customer(principal, profile)
    result["saved_vehicle_count"] = CustomerVehicle.objects.filter(owner=principal, active=True, saved=True).count()
    result["saved_address_count"] = CustomerAddress.objects.filter(owner=principal, active=True, saved=True).count()
    return result


def customer_jobs(actor, customer_id, *, limit=50, before=None):
    rbac.require(actor, "users.read")
    rbac.require(actor, "jobs.read")
    if not Principal.objects.filter(pk=customer_id, plane="app").exists():
        reject(404, "CUSTOMER_NOT_FOUND", "Customer not found")
    qs = ServiceJob.objects.filter(request__customer_id=customer_id).order_by("-id")
    if before:
        qs = qs.filter(id__lt=before)
    rows = list(qs[:_page_limit(limit)+1])
    more = len(rows) > limit
    rows = rows[:limit]
    return {"items": [{"job_id": str(row.pk), "request_id": str(row.request_id),
                       "state": row.state, "version": row.version,
                       "created_at": row.created_at.isoformat()} for row in rows],
            "next_cursor": str(rows[-1].pk) if more and rows else None}


def list_drivers(actor, *, limit=50, before=None, status=None, email=None):
    rbac.require(actor, "drivers.read")
    batch = list(_base("driver", status=status, email=email, before=before)[:_page_limit(limit)+1])
    more = len(batch) > limit
    batch = batch[:limit]
    profiles = _profiles(DriverProfile, [p.pk for p in batch])
    return {"items": [_driver(p, profiles.get(p.pk)) for p in batch],
            "next_cursor": str(batch[-1].pk) if more and batch else None}


def driver_detail(actor, driver_id):
    rbac.require(actor, "drivers.read")
    principal = Principal.objects.filter(pk=driver_id, plane="driver").select_related("account").first()
    if principal is None:
        reject(404, "DRIVER_NOT_FOUND", "Driver not found")
    profile = DriverProfile.objects.filter(principal=principal).first()
    result = _driver(principal, profile)
    # These facts are not proof of safety, eligibility or dispatch availability.
    result["active_duty_count"] = profile.dutysession_set.filter(ended_at__isnull=True).count() if profile else 0
    return result
