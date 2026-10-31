"""Actual PostgreSQL/HTTP tests: PENDING driver authentication is not dispatch approval."""
from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.auth.hashers import make_password
from django.test import Client
from django.utils import timezone

from cow.errors import DomainError
from cow.models import Account, AuthChallenge, AuthSession, Principal
from cow.services import auth

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]
PASSWORD = "Valid-Driver-Password-123!"


def new_actor(plane="driver", status="PENDING", verified=True, two_factor=False):
    a = Account.objects.create(
        email=f"driver-auth-{uuid4().hex}@example.test",
        password_hash=make_password(PASSWORD),
        email_verified=verified,
        two_factor_enabled=two_factor,
    )
    return Principal.objects.create(account=a, plane=plane, status=status)


def test_pending_driver_login_me_refresh_and_absolute_expiry():
    p = new_actor()
    result = auth.login("driver", p.account.email, PASSWORD)
    assert result["two_factor_required"] is False
    tokens = result["tokens"]
    assert auth.authenticate(tokens["access_token"], "driver").id == p.id
    response = Client().get("/api/v1/driver/me", HTTP_AUTHORIZATION=f"Bearer {tokens['access_token']}")
    assert response.status_code == 200, response.content
    assert response.json()["data"]["status"] == "PENDING"
    refreshed, error = auth.refresh("driver", tokens["refresh_token"])
    assert error is None, error
    assert refreshed["tokens"]["access_token"] != tokens["access_token"]
    session = AuthSession.objects.get(access_digest=auth.digest(refreshed["tokens"]["access_token"]))
    session.absolute_expires_at = timezone.now() - timedelta(seconds=2)
    session.save(update_fields=["absolute_expires_at", "updated_at"])
    with pytest.raises(DomainError):
        auth.authenticate(refreshed["tokens"]["access_token"], "driver")
    denied, error = auth.refresh("driver", refreshed["tokens"]["refresh_token"])
    assert denied is None and error == "UNAUTHORIZED"


def test_unverified_suspended_and_other_plane_pending_remain_ineligible():
    for plane, status, verified in (
        ("driver", "PENDING", False), ("driver", "SUSPENDED", True),
        ("admin", "PENDING", True), ("app", "PENDING", True),
    ):
        p = new_actor(plane, status, verified)
        with pytest.raises(DomainError):
            auth.login(plane, p.account.email, PASSWORD)
        issued = auth.make_tokens(p)
        with pytest.raises(DomainError):
            auth.authenticate(issued["access_token"], plane)
        renewed, err = auth.refresh(plane, issued["refresh_token"])
        assert renewed is None and err == "ACCOUNT_NOT_READY"


def test_pending_driver_2fa_completion_and_suspension_gate():
    p = new_actor(two_factor=True)
    challenge = AuthChallenge.objects.create(
        account=p.account, plane="driver", purpose="LOGIN_2FA", code_digest="",
        expires_at=timezone.now() + timedelta(minutes=5), last_sent_at=timezone.now(),
    )
    challenge.code_digest = auth.otp_digest(challenge.id, "024681")
    challenge.save(update_fields=["code_digest", "updated_at"])
    result = auth.verify_login_otp(challenge.id, "024681", "driver")
    assert result["two_factor_required"] is False
    token = result["tokens"]["access_token"]
    assert auth.authenticate(token, "driver").id == p.id
    p.status = "SUSPENDED"
    p.save(update_fields=["status", "updated_at"])
    with pytest.raises(DomainError):
        auth.authenticate(token, "driver")
