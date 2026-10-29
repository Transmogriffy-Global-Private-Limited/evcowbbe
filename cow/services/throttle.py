"""Durable anti-abuse bucket keyed with nonreversible HMAC identifiers."""
from datetime import timedelta
from hashlib import sha256
import hmac
from django.conf import settings
from django.db import transaction, connection
from django.utils import timezone
from cow.models import AuthRateBucket
from cow.errors import reject


def check(kind: str, identifier: str, max_hits: int, window_s: int):
    key=hmac.new(settings.SECRET_KEY.encode(),f"{kind}:{identifier}".encode(),sha256).hexdigest()
    with transaction.atomic():
        lock=int.from_bytes(bytes.fromhex(key)[:8],"big",signed=True)
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)",[lock])
        now=timezone.now()
        obj=AuthRateBucket.objects.filter(key_digest=key).first()
        if obj is None:
            AuthRateBucket.objects.create(key_digest=key,hits=1,window_ends_at=now+timedelta(seconds=window_s))
        elif obj.window_ends_at<=now:
            obj.hits=1
            obj.window_ends_at=now+timedelta(seconds=window_s)
            obj.save(update_fields=["hits","window_ends_at"])
        elif obj.hits >= max_hits:
            reject(429,"RATE_LIMITED","Too many requests; retry later")
        else:
            obj.hits+=1
            obj.save(update_fields=["hits"])
