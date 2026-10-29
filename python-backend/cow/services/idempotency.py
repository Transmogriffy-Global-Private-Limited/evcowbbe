"""Use PostgreSQL uniqueness + row lock; the call must execute in transaction.atomic()."""
import hashlib
import json
from datetime import timedelta
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from cow.models import IdempotencyReceipt
from cow.errors import reject


def canonical_digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def execute(principal, scope, key, payload, func, retention_hours=24):
    if not key or not (16 <= len(key) <= 128):
        reject(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key must be 16 to 128 characters")
    reqdig = canonical_digest(payload)
    with transaction.atomic():
        # Per-key transactional advisory lock serializes contenders before the receipt exists.
        # Hash collisions only cause extra serialization, never false receipts.
        lock_key = hashlib.sha256(f"{principal.id}:{scope}:{key}".encode()).digest()
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [int.from_bytes(lock_key[:8], "big", signed=True)])
        receipt = IdempotencyReceipt.objects.filter(actor=principal,scope=scope,key=key).first()
        if receipt:
            if receipt.request_digest != reqdig:
                reject(409, "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST", "Key used for different request")
            return receipt.status, receipt.response
        status, result = func()
        IdempotencyReceipt.objects.create(actor=principal, scope=scope, key=key,
            request_digest=reqdig, status=status, response=result,
            expires_at=timezone.now()+timedelta(hours=retention_hours))
        return status, result
