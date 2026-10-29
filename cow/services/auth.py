"""Opaque rotating credentials scoped to one principal plane; no JWT fallback."""
from datetime import timedelta
from hashlib import sha256
from secrets import token_urlsafe, randbelow
import hmac
from django.conf import settings
from cow.services.mail import enqueue_secret, ready as mail_ready
from cow.services import throttle
from django.contrib.auth.hashers import check_password, make_password
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from cow.models import (Account, Principal, AuthSession, ConsumedRefreshToken,
                        AuthActionToken, AuthChallenge, Outbox)
from cow.errors import reject

ACCESS_TTL = timedelta(seconds=settings.ACCESS_TOKEN_TTL_SECONDS)
REFRESH_TTL = timedelta(seconds=settings.REFRESH_TOKEN_TTL_SECONDS)
ABSOLUTE_TTL = timedelta(seconds=settings.SESSION_ABSOLUTE_TTL_SECONDS)


def digest(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if not (3 <= len(email) <= 254):
        reject(400, "VALIDATION_FAILED", "Invalid email")
    try:
        validate_email(email)
    except ValidationError:
        reject(400, "VALIDATION_FAILED", "Invalid email")
    return email


def validate_password(password: str) -> None:
    if len(password) < 12 or len(password) > 256:
        reject(400, "VALIDATION_FAILED", "Password does not meet length policy")


def issue_action(account: Account, kind: str):
    now = timezone.now()
    token = token_urlsafe(36)
    expiry=now + (timedelta(hours=24) if kind == "VERIFY_EMAIL" else timedelta(minutes=20))
    AuthActionToken.objects.create(digest=digest(token), account=account, kind=kind,
                                   expires_at=expiry)
    enqueue_secret(account.email, kind, token, f"{kind}:{account.id}:{digest(token)}", expires_at=expiry)
    return token


def register(plane: str, email: str, password: str):
    if plane not in ("app", "driver"):
        reject(403, "FORBIDDEN", "Public admin registration disabled")
    validate_password(password)
    email = normalize_email(email)
    if not mail_ready():
        reject(503, "MAIL_PROVIDER_UNAVAILABLE", "Verification delivery unavailable")
    throttle.check("register",email,4,3600)
    with transaction.atomic():
        if Account.objects.filter(email=email).exists():
            return {"message": "If eligible, a verification email has been queued", "plane": plane}
        account = Account.objects.create(email=email, password_hash=make_password(password))
        Principal.objects.create(account=account, plane=plane, status="ACTIVE" if plane == "app" else "PENDING")
        issue_action(account, "VERIFY_EMAIL")
    return {"message": "If eligible, a verification email has been queued", "plane": plane}


def verify_email(token: str):
    if not token or len(token) > 200:
        reject(400, "VALIDATION_FAILED", "Invalid token")
    with transaction.atomic():
        a = AuthActionToken.objects.select_for_update().filter(digest=digest(token), kind="VERIFY_EMAIL").first()
        if not a or a.consumed_at or a.expires_at <= timezone.now():
            reject(400, "TOKEN_INVALID", "Token invalid or expired")
        a.consumed_at = timezone.now()
        a.save(update_fields=["consumed_at", "updated_at"])
        Account.objects.filter(pk=a.account_id).update(email_verified=True)
    return {"message": "Email verified"}


def make_tokens(principal: Principal, session: AuthSession | None = None):
    now = timezone.now()
    access, refresh = token_urlsafe(48), token_urlsafe(48)
    if session is None:
        session = AuthSession.objects.create(
            principal=principal, access_digest=digest(access), refresh_digest=digest(refresh),
            access_expires_at=now + ACCESS_TTL, refresh_expires_at=now + REFRESH_TTL,
            absolute_expires_at=now + ABSOLUTE_TTL)
    else:
        session.access_digest = digest(access)
        session.refresh_digest = digest(refresh)
        session.access_expires_at = now + ACCESS_TTL
        session.refresh_expires_at = min(now + REFRESH_TTL, session.absolute_expires_at)
        session.refresh_generation += 1
        session.save(update_fields=["access_digest", "refresh_digest", "access_expires_at", "refresh_expires_at", "refresh_generation", "updated_at"])
    return {"access_token": access, "refresh_token": refresh,
            "token_type": "Bearer", "expires_in": int(ACCESS_TTL.total_seconds())}


def login(plane: str, email: str, password: str):
    email = normalize_email(email)
    throttle.check("login",email,8,300)
    account = Account.objects.filter(email=email).first()
    # Always run an Argon2 verification to reduce account-existence timing leaks.
    dummy = '$argon2id$v=19$m=65536,t=3,p=1$YWJjZGVmZ2hpamtsbW5vcA$L9tlpO6oT6ojBtXSWYEP8SlwjtEld0MuYfctuOtx0po'
    correct = check_password(password, account.password_hash if account else dummy)
    if not account or not correct:
        reject(401, "INVALID_CREDENTIALS", "Invalid credentials")
    principal = Principal.objects.filter(account=account, plane=plane, status="ACTIVE").first()
    if not principal or not account.email_verified:
        reject(403, "ACCOUNT_NOT_READY", "Account is not eligible to sign in")
    if account.two_factor_enabled:
        challenge = create_challenge(account, plane, "LOGIN_2FA")
        return {"two_factor_required": True, "challenge_id": str(challenge.id)}
    with transaction.atomic():
        tokens = make_tokens(principal)
    return {"two_factor_required": False, "tokens": tokens}


def authenticate(token: str, expected_plane: str) -> Principal:
    if not token or len(token) > 256:
        reject(401, "UNAUTHORIZED", "Invalid authentication")
    session = AuthSession.objects.select_related("principal", "principal__account").filter(
        access_digest=digest(token), revoked_at__isnull=True, access_expires_at__gt=timezone.now()).first()
    if not session:
        reject(401, "UNAUTHORIZED", "Invalid authentication")
    if session.principal.plane != expected_plane:
        reject(403, "PRINCIPAL_PLANE_MISMATCH", "This token belongs to another application plane")
    if session.principal.status != "ACTIVE" or not session.principal.account.email_verified:
        reject(403, "ACCOUNT_NOT_READY", "Principal not active")
    return session.principal


def refresh(plane: str, token: str):
    if not token or len(token) > 256:
        reject(401, "UNAUTHORIZED", "Invalid refresh token")
    dig = digest(token)
    with transaction.atomic():
        prior = ConsumedRefreshToken.objects.select_related("session").filter(digest=dig).first()
        if prior:
            # An attacker replaying an old refresh must revoke even when response is rejected.
            AuthSession.objects.filter(pk=prior.session_id, revoked_at__isnull=True).update(revoked_at=timezone.now())
            return None, "REFRESH_TOKEN_REUSED"
        session = AuthSession.objects.select_for_update().select_related("principal", "principal__account").filter(refresh_digest=dig).first()
        if not session or session.revoked_at or session.absolute_expires_at <= timezone.now() or session.refresh_expires_at <= timezone.now():
            return None, "UNAUTHORIZED"
        if session.principal.plane != plane:
            return None, "PRINCIPAL_PLANE_MISMATCH"
        if session.principal.status != "ACTIVE" or not session.principal.account.email_verified:
            return None, "ACCOUNT_NOT_READY"
        ConsumedRefreshToken.objects.create(digest=dig, session=session)
        return {"two_factor_required": False, "tokens": make_tokens(session.principal, session)}, None


def logout(token: str, all_sessions=False):
    with transaction.atomic():
        session = AuthSession.objects.select_for_update().filter(access_digest=digest(token)).first()
        if not session or session.revoked_at:
            reject(401, "UNAUTHORIZED", "Invalid authentication")
        if all_sessions:
            AuthSession.objects.filter(principal__account_id=session.principal.account_id, revoked_at__isnull=True).update(revoked_at=timezone.now())
        else:
            session.revoked_at = timezone.now()
            session.save(update_fields=["revoked_at", "updated_at"])
    return {"message": "Logged out"}


def change_password(principal: Principal, current: str, new: str):
    validate_password(new)
    with transaction.atomic():
        account = Account.objects.select_for_update().get(id=principal.account_id)
        if not check_password(current, account.password_hash):
            reject(403, "INVALID_CREDENTIALS", "Current password not accepted")
        account.password_hash = make_password(new)
        account.save(update_fields=["password_hash", "updated_at"])
        AuthSession.objects.filter(principal__account=account, revoked_at__isnull=True).update(revoked_at=timezone.now())
    return {"message": "Password changed; sessions revoked"}


def otp_digest(challenge_id, code):
    if len(settings.COW_OTP_PEPPER) < 32:
        reject(503, "OTP_UNAVAILABLE", "One-time codes unavailable")
    return hmac.new(settings.COW_OTP_PEPPER.encode(),f"{challenge_id}:{code}".encode(),sha256).hexdigest()


def create_challenge(account: Account, plane: str, purpose: str):
    if not mail_ready():
        reject(503,"MAIL_PROVIDER_UNAVAILABLE","Email verification unavailable")
    with transaction.atomic():
        now=timezone.now()
        challenge=AuthChallenge.objects.create(account=account,plane=plane,purpose=purpose,
            code_digest="",expires_at=now+timedelta(seconds=settings.OTP_TTL_SECONDS),last_sent_at=now)
        code=f"{randbelow(1000000):06d}"
        challenge.code_digest=otp_digest(challenge.id,code)
        challenge.save(update_fields=["code_digest","updated_at"])
        enqueue_secret(account.email,purpose,code,f"otp:{challenge.id}:0",expires_at=challenge.expires_at)
    return challenge


def check_challenge(challenge_id,code,purpose,plane,account_id=None):
    with transaction.atomic():
        challenge=AuthChallenge.objects.select_for_update().filter(pk=challenge_id,purpose=purpose,plane=plane).first()
        if challenge is None or (account_id is not None and challenge.account_id!=account_id):
            reject(400,"OTP_INVALID","Invalid or expired code")
        now=timezone.now()
        if challenge.consumed_at or challenge.expires_at<=now or challenge.attempts>=5:
            reject(400,"OTP_INVALID","Invalid or expired code")
        challenge.attempts+=1
        valid=hmac.compare_digest(challenge.code_digest,otp_digest(challenge.id,code))
        if valid:
            challenge.consumed_at=now
        challenge.save(update_fields=["attempts","consumed_at","updated_at"])
        # Avoid transaction rollback of attempt increment on failure.
        return challenge if valid else None


def verify_login_otp(challenge_id,code,plane):
    ch=check_challenge(challenge_id,code,"LOGIN_2FA",plane)
    if ch is None:reject(400,"OTP_INVALID","Invalid or expired code")
    principal=Principal.objects.filter(account_id=ch.account_id,plane=plane,status="ACTIVE").first()
    if not principal:reject(403,"ACCOUNT_NOT_READY","Principal not active")
    with transaction.atomic():
        return {"two_factor_required":False,"tokens":make_tokens(principal)}


def begin_two_factor(principal,password,enable):
    account=principal.account
    if not check_password(password,account.password_hash):
        reject(403,"INVALID_CREDENTIALS","Password incorrect")
    if account.two_factor_enabled==enable:
        reject(409,"TWO_FACTOR_STATE_CONFLICT","2FA is already in target state")
    ch=create_challenge(account,principal.plane,"ENABLE_2FA" if enable else "DISABLE_2FA")
    return {"challenge_id":str(ch.id),"message":"Check email for one-time code"}


def confirm_two_factor(principal,challenge_id,code,enable):
    ch=check_challenge(challenge_id,code,"ENABLE_2FA" if enable else "DISABLE_2FA",principal.plane,principal.account_id)
    if ch is None:reject(400,"OTP_INVALID","Invalid or expired code")
    with transaction.atomic():
        Account.objects.filter(pk=principal.account_id).update(two_factor_enabled=enable)
        AuthSession.objects.filter(principal__account_id=principal.account_id,revoked_at__isnull=True).update(revoked_at=timezone.now())
    return {"two_factor_enabled":enable,"sessions_revoked":True}


def resend_otp(challenge_id,plane):
    if not mail_ready():reject(503,"MAIL_PROVIDER_UNAVAILABLE","OTP email unavailable")
    throttle.check("otp-resend",str(challenge_id),5,600)
    with transaction.atomic():
        ch=AuthChallenge.objects.select_for_update().filter(pk=challenge_id,plane=plane).first()
        now=timezone.now()
        if not ch or ch.consumed_at or ch.expires_at<=now or ch.attempts>=settings.OTP_MAX_ATTEMPTS:
            reject(400,"OTP_INVALID","Invalid or expired code")
        if (now-ch.last_sent_at).total_seconds()<settings.OTP_RESEND_COOLDOWN_SECONDS:
            reject(429,"OTP_RESEND_COOLDOWN","Try again later")
        code=f"{randbelow(1000000):06d}"
        ch.code_digest=otp_digest(ch.id,code)
        ch.last_sent_at=now
        ch.save(update_fields=["code_digest","last_sent_at","updated_at"])
        enqueue_secret(ch.account.email,ch.purpose,code,f"otp:{ch.id}:{now.timestamp()}",expires_at=ch.expires_at)
    return {"message":"If eligible, a code has been queued"}


def resend_verification(email):
    if not mail_ready():reject(503,"MAIL_PROVIDER_UNAVAILABLE","Verification email unavailable")
    throttle.check("resend-verify",normalize_email(email),3,3600)
    with transaction.atomic():
        account=Account.objects.select_for_update().filter(email=normalize_email(email),email_verified=False).first()
        if account:
            issue_action(account,"VERIFY_EMAIL")
    return {"message":"If eligible, a verification email has been queued"}


def forgot_password(email):
    if not mail_ready():reject(503,"MAIL_PROVIDER_UNAVAILABLE","Password reset email unavailable")
    throttle.check("forgot",normalize_email(email),3,3600)
    with transaction.atomic():
        account=Account.objects.select_for_update().filter(email=normalize_email(email)).first()
        if account:
            issue_action(account,"RESET_PASSWORD")
    return {"message":"If eligible, a password reset email has been queued"}


def reset_password(token,password):
    validate_password(password)
    with transaction.atomic():
        row=AuthActionToken.objects.select_for_update().filter(digest=digest(token),kind="RESET_PASSWORD").first()
        if not row or row.consumed_at or row.expires_at<=timezone.now():
            reject(400,"TOKEN_INVALID","Invalid or expired reset token")
        account=Account.objects.select_for_update().get(pk=row.account_id)
        account.password_hash=make_password(password)
        account.save(update_fields=["password_hash","updated_at"])
        row.consumed_at=timezone.now()
        row.save(update_fields=["consumed_at","updated_at"])
        AuthSession.objects.filter(principal__account=account,revoked_at__isnull=True).update(revoked_at=timezone.now())
    return {"message":"Password reset; previous sessions revoked"}
