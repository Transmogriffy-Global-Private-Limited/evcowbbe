"""Durable, encrypted-at-rest credentials and bounded SMTP transport.

Mail delivery attempts never cause auth/commercial facts to be fabricated.
Requires externally configured TLS-enabled real SMTP, no local Mailpit.
"""
import ssl
import smtplib
import hashlib
from datetime import timedelta
from email.message import EmailMessage
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from cow.errors import reject
from cow.models import Outbox


def ready():
    return bool(settings.COW_SMTP_HOST and settings.COW_SMTP_FROM and settings.COW_EMAIL_CIPHER_KEY
                and settings.COW_OTP_PEPPER and len(settings.COW_OTP_PEPPER) >= 32)


def enqueue_secret(address: str, purpose: str, secret: str, event_key: str, *, expires_at):
    if not ready():
        reject(503,"MAIL_PROVIDER_UNAVAILABLE","Verification delivery unavailable")
    try:
        encrypted=Fernet(settings.COW_EMAIL_CIPHER_KEY.encode()).encrypt(secret.encode()).decode()
    except (ValueError,TypeError):
        reject(503,"MAIL_PROVIDER_UNAVAILABLE","Mail encryption not configured")
    Outbox.objects.create(kind="AUTH_EMAIL_TOKEN",destination=address,event_key=event_key,
                          payload={"purpose":purpose,"ciphertext":encrypted,"expires_at":expires_at.isoformat()},available_at=timezone.now())


def claim_next():
    with transaction.atomic():
        now=timezone.now()
        row=(Outbox.objects.select_for_update(skip_locked=True)
             .filter(Q(state="PENDING") | Q(state="LEASED",leased_until__lte=now), available_at__lte=now)
             .order_by("available_at","id").first())
        if not row or (row.state=="LEASED" and row.leased_until and row.leased_until > now):
            return None
        row.state="LEASED"
        row.leased_until=now+timedelta(minutes=2)
        row.attempts+=1
        row.save(update_fields=["state","leased_until","attempts","updated_at"])
        return row.id


def deliver_one(id_):
    entry=Outbox.objects.get(pk=id_)
    if entry.state!="LEASED":
        return False
    try:
        from datetime import datetime
        if datetime.fromisoformat(entry.payload["expires_at"]) <= timezone.now():
            Outbox.objects.filter(id=id_,state="LEASED").update(state="EXPIRED",payload={"purpose":entry.payload["purpose"]})
            return False
        plaintext=Fernet(settings.COW_EMAIL_CIPHER_KEY.encode()).decrypt(entry.payload["ciphertext"].encode()).decode()
        msg=EmailMessage()
        msg["From"]=settings.COW_SMTP_FROM
        msg["To"]=entry.destination
        msg["Subject"]="TransEV Charger on Wheels: "+entry.payload["purpose"].replace("_"," ").title()
        msg.set_content(f"Purpose: {entry.payload['purpose']}\nCode/token: {plaintext}\nNever share this code.\n")
        if settings.COW_SMTP_TLS_MODE=="tls":
            conn=smtplib.SMTP_SSL(settings.COW_SMTP_HOST,settings.COW_SMTP_PORT,timeout=15,context=ssl.create_default_context())
        else:
            conn=smtplib.SMTP(settings.COW_SMTP_HOST,settings.COW_SMTP_PORT,timeout=15)
        with conn:
            if settings.COW_SMTP_TLS_MODE=="starttls":
                conn.ehlo()
                conn.starttls(context=ssl.create_default_context())
                conn.ehlo()
            if settings.COW_SMTP_USER:
                conn.login(settings.COW_SMTP_USER,settings.COW_SMTP_PASSWORD)
            conn.send_message(msg)
        with transaction.atomic():
            row=Outbox.objects.select_for_update().get(pk=id_)
            if row.state=="LEASED":
                row.state="SENT"
                row.delivered_at=timezone.now()
                row.payload={"purpose":entry.payload["purpose"],"delivered":True}
                row.save(update_fields=["state","delivered_at","payload","updated_at"])
        return True
    except (smtplib.SMTPException,OSError,ValueError,KeyError,InvalidToken):
        with transaction.atomic():
            row=Outbox.objects.select_for_update().get(pk=id_)
            if row.state=="LEASED":
                row.state="DEAD" if row.attempts>=8 else "PENDING"
                row.available_at=timezone.now()+timedelta(seconds=min(3600,30*(2**min(row.attempts,7))))
                row.save(update_fields=["state","available_at","updated_at"])
        return False
