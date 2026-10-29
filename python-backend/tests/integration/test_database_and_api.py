"""Real PostgreSQL tests: NEVER use application/live DB or SQLite stand-ins."""
import json
import pytest
from django.test import Client
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.contrib.auth.hashers import make_password
from datetime import timedelta
from uuid import uuid4
from cow.models import (Account, Principal, AuthSession, AdminRole, AdminOverride,
                        CustomerVehicle, CustomerAddress, ServiceRequest, PricingPolicy,
                        IdempotencyReceipt, ServiceVehicle, Charger, DutySession, DriverProfile)
from cow.services import auth, idempotency, rbac

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]


def principal(plane="app"):
    a=Account.objects.create(email=f"{uuid4().hex}@example.test",password_hash=make_password("MyStrongRandomPass123!"),email_verified=True)
    return Principal.objects.create(account=a,plane=plane,status="ACTIVE")


def headers(p):
    access="test-access-"+uuid4().hex
    AuthSession.objects.create(principal=p,access_digest=auth.digest(access),refresh_digest=auth.digest(uuid4().hex),
        access_expires_at=timezone.now()+timedelta(minutes=15),
        refresh_expires_at=timezone.now()+timedelta(days=1),absolute_expires_at=timezone.now()+timedelta(days=30))
    return {"HTTP_AUTHORIZATION":"Bearer "+access}


def test_cross_plane_customer_vehicle_blocked_by_db_trigger():
    driver=principal("driver")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CustomerVehicle.objects.create(owner=driver,make="Example",model="D",connector_types=["CCS2"])


def test_admin_explicit_deny_wins_over_full_admin():
    p=principal("admin")
    AdminRole.objects.create(principal=p,role="FULL_ADMIN")
    assert rbac.permitted({"FULL_ADMIN"},{"fleet.manage":"DENY"},"fleet.manage") is False
    AdminOverride.objects.create(principal=p,capability="fleet.manage",decision="DENY")
    with pytest.raises(Exception):rbac.require(p,"fleet.manage")


def test_refresh_rotation_and_replay_revokes_family():
    p=principal()
    initial=auth.make_tokens(p)
    new,err=auth.refresh("app",initial["refresh_token"])
    assert err is None
    assert new["tokens"]["refresh_token"] != initial["refresh_token"]
    result,err=auth.refresh("app",initial["refresh_token"])
    assert result is None and err=="REFRESH_TOKEN_REUSED"
    assert AuthSession.objects.get(refresh_digest=auth.digest(new["tokens"]["refresh_token"])).revoked_at is not None


def test_route_plane_isolation_and_owned_reads():
    customer1=principal()
    customer2=principal()
    h1=headers(customer1)
    h2=headers(customer2)
    client=Client()
    invalid=client.get("/api/v1/driver/me",**h1)
    assert invalid.status_code==403
    create=client.post("/api/v1/app/vehicles",json.dumps({"make":"Test","model":"EV","supports_ac":True}),content_type="application/json",**h1)
    assert create.status_code==201, create.content
    owner_read=client.get("/api/v1/app/vehicles",**h1).json()
    other_read=client.get("/api/v1/app/vehicles",**h2).json()
    assert len(owner_read["data"])==1
    assert other_read["data"]==[]


def test_idempotency_repeat_and_conflicting_request():
    p=principal()
    called=[]
    def mutate():
        called.append(1)
        return 201,{"data":{"id":"fixed"}}
    assert idempotency.execute(p,"test","a"*16,{"amount":100},mutate)[0]==201
    assert idempotency.execute(p,"test","a"*16,{"amount":100},mutate)[0]==201
    assert len(called)==1
    with pytest.raises(Exception):idempotency.execute(p,"test","a"*16,{"amount":101},mutate)


def test_duty_claim_unique_postgres_partial_index():
    p1,p2=principal("driver"),principal("driver")
    d1=DriverProfile.objects.create(principal=p1,eligibility="ACTIVE")
    d2=DriverProfile.objects.create(principal=p2,eligibility="ACTIVE")
    vehicle=ServiceVehicle.objects.create(asset_tag="vehicle-one",make="Example",model="Truck")
    charger1=Charger.objects.create(asset_tag="charger-one",hal_identity="physical-one",manufacturer="X",model="Y",rated_power_w=30000)
    charger2=Charger.objects.create(asset_tag="charger-two",hal_identity="physical-two",manufacturer="X",model="Y",rated_power_w=30000)
    DutySession.objects.create(driver=d1,vehicle=vehicle,charger=charger1,started_at=timezone.now())
    with pytest.raises(IntegrityError):
        with transaction.atomic():DutySession.objects.create(driver=d2,vehicle=vehicle,charger=charger2,started_at=timezone.now())


def stage_job_for_hal():
    from cow.models import Quote, ServiceJob, HALCommand, Charger
    p=principal("app")
    req=ServiceRequest.objects.create(customer=p,vehicle_snapshot={"make":"Test"},
      address_snapshot={"address":"Test"},requested_energy_wh=5000,state="ACCEPTED")
    policy=PricingPolicy.objects.create(name="Test tariff",revision=1,status="ACTIVE",currency="INR",
       base_service_paise=5000,per_kwh_paise=1400,effective_from=timezone.now())
    quote=Quote.objects.create(request=req,pricing_policy=policy,pricing_snapshot={"revision":1},
       total_paise=12000,expires_at=timezone.now()+timedelta(hours=1),accepted_at=timezone.now())
    job=ServiceJob.objects.create(request=req,accepted_quote=quote,state="SERVICE_PREPARING")
    charger=Charger.objects.create(asset_tag="C-ONE",hal_identity="physical-001",manufacturer="Test",model="C",rated_power_w=30000)
    command=HALCommand.objects.create(job=job,charger=charger,action="StartCharging",params={"connector_number":1},state="HAL_ACCEPTED")
    return job,charger,command


def test_hal_fact_out_of_order_duplicate_and_physical_truth():
    from cow.hal.ingress import ingest_fact
    from cow.hal.simulator import FakeHAL
    from cow.hal.contract import make_command
    from cow.models import ChargingSession, MeterObservation, HALFact
    job,charger,command=stage_job_for_hal()
    fake=FakeHAL()
    fake.accept(make_command(command.id,charger.hal_identity,"StartCharging",{"connector_number":1}))
    started=fake.start_physical(str(command.id),"physical-TX-111",1)
    meter=fake.meter("physical-TX-111",1200)
    # network delivered meter before start; retain evidence without claiming job started
    assert ingest_fact(meter)["outcome"]=="DEFERRED"
    job.refresh_from_db()
    assert job.state=="SERVICE_PREPARING"
    assert ChargingSession.objects.count()==0
    result=ingest_fact(started)
    assert result["outcome"]=="RECORDED"
    job.refresh_from_db()
    assert job.state=="CHARGING"
    assert ChargingSession.objects.count()==1
    assert MeterObservation.objects.get().energy_wh==1200
    assert ingest_fact(started)["outcome"]=="DUPLICATE"
    assert MeterObservation.objects.count()==1
    end=fake.end("physical-TX-111")
    assert ingest_fact(end)["outcome"]=="RECORDED"
    assert ChargingSession.objects.get().state=="ENDED"
    job.refresh_from_db()
    assert job.state=="CHARGING"  # end is NOT business job completion


def test_database_prevents_mutating_historical_quote_terms():
    from cow.models import Quote
    job,charger,command=stage_job_for_hal()
    q=job.accepted_quote
    q.total_paise+=1
    with pytest.raises(IntegrityError):
        with transaction.atomic():q.save()
