"""Django settings bound to the existing VPS2 /etc/evcowbbe/*.env contract.

No independent COW_* credential aliases and no automatic migration on startup.
"""
import os
from pathlib import Path

from config.legacy_env import checked_int, derive_key, listen, postgres, public_host, smtp_mode

BASE_DIR = Path(__file__).resolve().parent.parent
ENV = os.environ.get("APP_ENV", "development")
if ENV not in {"development", "staging", "production", "test"}:
    raise ValueError("Invalid APP_ENV")
# Existing immutable peppers are consumed under domain separation. No new
# plaintext Django/Fernet secret is required in the existing env files.
SECRET_KEY = derive_key(os.environ, "django-signing")
COW_EMAIL_CIPHER_KEY = derive_key(os.environ, "encrypted-outbox", urlsafe=True)
# Domain-separated from email ciphertext; same accepted pepper source, different key.
COW_HAL_V1_CHARGE_CIPHER_KEY = derive_key(os.environ, "hal-v1-one-use-credential", urlsafe=True)
COW_OTP_PEPPER = os.environ["OTP_HMAC_PEPPER"]
PASSWORD_PEPPER = os.environ["PASSWORD_PEPPER"]
DEBUG = False
PUBLIC_HOST, ALLOWED_HOSTS = public_host(os.environ)
COW_PUBLIC_APP_URL = os.environ["PUBLIC_BASE_URL"]
HTTP_LISTEN_ADDR = listen(os.environ)
DATABASES = {"default": postgres(os.environ)}
DB_MAX_CONNS = checked_int(os.environ, "DB_MAX_CONNS", 10, 1, 100)
INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth", "django.contrib.staticfiles", "ninja", "cow"]
MIDDLEWARE = ["config.middleware.RequestSecurityMiddleware"]
ROOT_URLCONF = "config.urls"
# Django Ninja Swagger renders app-provided Django templates even with DEBUG=False.
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]
STATIC_URL = "/static/"
USE_TZ = True
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
PASSWORD_HASHERS = ["cow.passwords.PepperedArgon2PasswordHasher"]
PASSWORD_ARGON2_MEMORY_KIB = checked_int(os.environ,"PASSWORD_ARGON2_MEMORY_KIB",65536,8192,1048576)
PASSWORD_ARGON2_ITERATIONS = checked_int(os.environ,"PASSWORD_ARGON2_ITERATIONS",3,1,20)
PASSWORD_ARGON2_PARALLELISM = checked_int(os.environ,"PASSWORD_ARGON2_PARALLELISM",1,1,16)
ACCESS_TOKEN_TTL_SECONDS = checked_int(os.environ,"ACCESS_TOKEN_TTL_SECONDS",900,60,86400)
REFRESH_TOKEN_TTL_SECONDS = checked_int(os.environ,"REFRESH_TOKEN_TTL_SECONDS",2592000,3600,7776000)
SESSION_ABSOLUTE_TTL_SECONDS = checked_int(os.environ,"SESSION_ABSOLUTE_TTL_SECONDS",7776000,3600,31536000)
OTP_TTL_SECONDS = checked_int(os.environ,"OTP_TTL_SECONDS",600,60,3600)
OTP_MAX_ATTEMPTS = checked_int(os.environ,"OTP_MAX_ATTEMPTS",5,1,20)
OTP_RESEND_COOLDOWN_SECONDS = checked_int(os.environ,"OTP_RESEND_COOLDOWN_SECONDS",60,10,3600)
RATE_AUTH_PER_MINUTE = checked_int(os.environ,"RATE_AUTH_PER_MINUTE",10,1,10000)
RATE_OTP_SEND_PER_HOUR = checked_int(os.environ,"RATE_OTP_SEND_PER_HOUR",6,1,10000)
RATE_REFRESH_PER_MINUTE = checked_int(os.environ,"RATE_REFRESH_PER_MINUTE",12,1,10000)
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
# Same-origin browser clients need no CORS. The FE's actual cross-origin
# contract must be measured from the running Go API before cutover; do NOT
# automatically permit all origins or use untrusted Origin reflection.
COW_CORS_ORIGINS = frozenset(x.strip() for x in os.environ.get("COW_CORS_ORIGINS", "").split(",") if x.strip())
COW_HAL_SHARED_SECRET = os.environ.get("COW_HAL_SHARED_SECRET", "")
COW_HAL_BASE_URL = os.environ.get("COW_HAL_BASE_URL", "")

# Dedicated CoW Go HAL v1 instance: NEVER reuse the new CMS HAL/database/tokens.
def _cow_hal_strict_flag(name):
    value = os.environ.get(name, 'false').lower()
    if value not in {'true','false'}:
        raise ValueError('Invalid boolean configuration: ' + name)
    return value == 'true'

COW_HAL_V1_BASE_URL = os.environ.get('COW_HAL_V1_BASE_URL', '')
COW_HAL_V1_SCOPE_ID = os.environ.get('COW_HAL_V1_SCOPE_ID', '')
COW_HAL_V1_CMS_BEARER_TOKEN = os.environ.get('COW_HAL_V1_CMS_BEARER_TOKEN', '')
COW_HAL_V1_FACT_BEARER_TOKEN = os.environ.get('COW_HAL_V1_FACT_BEARER_TOKEN', '')
COW_HAL_V1_TRACE_BEARER_TOKEN = os.environ.get('COW_HAL_V1_TRACE_BEARER_TOKEN', '')
COW_HAL_V1_MAPPING_SYNC_ENABLED = _cow_hal_strict_flag('COW_HAL_V1_MAPPING_SYNC_ENABLED')
COW_HAL_V1_ADMIN_OPERATIONS_ENABLED = _cow_hal_strict_flag('COW_HAL_V1_ADMIN_OPERATIONS_ENABLED')
# Source-only: new customer charging starts remain OFF until approved commerce,
# location proof, operational authorization and live integration gates pass.
COW_HAL_V1_NEW_STARTS_ENABLED = _cow_hal_strict_flag('COW_HAL_V1_NEW_STARTS_ENABLED')
COW_HAL_V1_CONNECTION_STALE_SECONDS = checked_int(os.environ, 'COW_HAL_V1_CONNECTION_STALE_SECONDS', 900, 60, 86400)
COW_HAL_V1_METER_STALE_SECONDS = checked_int(os.environ, 'COW_HAL_V1_METER_STALE_SECONDS', 120, 10, 3600)
COW_HAL_V1_SOC_STALE_SECONDS = checked_int(os.environ, 'COW_HAL_V1_SOC_STALE_SECONDS', 120, 10, 3600)
COW_HAL_V1_CONNECTOR_STALE_SECONDS = checked_int(os.environ, 'COW_HAL_V1_CONNECTOR_STALE_SECONDS', 900, 60, 86400)

# Fail closed before startup if a CoW Go-HAL endpoint is configured without
# an independently authenticated, stable charging network scope.
if COW_HAL_V1_BASE_URL:
    from uuid import UUID as _CowHALUUID
    try:
        _cow_hal_scope = _CowHALUUID(COW_HAL_V1_SCOPE_ID)
        if _cow_hal_scope.int == 0 or str(_cow_hal_scope) != COW_HAL_V1_SCOPE_ID:
            raise ValueError('noncanonical HAL scope')
    except (TypeError, ValueError, AttributeError) as _exc:
        raise ValueError('Dedicated CoW HAL scope must be canonical nonzero UUID') from _exc
    _cow_hal_secrets = (COW_HAL_V1_CMS_BEARER_TOKEN, COW_HAL_V1_FACT_BEARER_TOKEN)
    if any(len(secret) < 32 for secret in _cow_hal_secrets) or len(set(_cow_hal_secrets)) != 2:
        raise ValueError('CoW HAL v1 requires distinct command and fact 32+ character tokens')
    if COW_HAL_V1_TRACE_BEARER_TOKEN:
        if len(COW_HAL_V1_TRACE_BEARER_TOKEN) < 32 or COW_HAL_V1_TRACE_BEARER_TOKEN in _cow_hal_secrets:
            raise ValueError('CoW HAL trace token must be independently strong and distinct')
        _cow_hal_secrets += (COW_HAL_V1_TRACE_BEARER_TOKEN,)
    if COW_HAL_SHARED_SECRET and COW_HAL_SHARED_SECRET in _cow_hal_secrets:
        raise ValueError('CoW HAL v1 cannot reuse the legacy HAL credential')
if (COW_HAL_V1_MAPPING_SYNC_ENABLED or COW_HAL_V1_ADMIN_OPERATIONS_ENABLED or COW_HAL_V1_NEW_STARTS_ENABLED) and not COW_HAL_V1_BASE_URL:
    raise ValueError('Go HAL operations cannot be enabled without a dedicated CoW HAL endpoint')
COW_SMTP_HOST = os.environ.get("SMTP_HOST", "")
COW_SMTP_PORT = checked_int(os.environ, "SMTP_PORT", 587, 1, 65535)
COW_SMTP_USER = os.environ.get("SMTP_USERNAME", "")
COW_SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
COW_SMTP_FROM = os.environ.get("MAIL_FROM_ADDRESS", "")
COW_SMTP_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "TransEV")
COW_SMTP_TLS_MODE = smtp_mode(os.environ)

if COW_HAL_V1_NEW_STARTS_ENABLED and not COW_HAL_V1_MAPPING_SYNC_ENABLED:
    raise ValueError('New CoW HAL charging requires explicit mapping-synchronization capability')

# Source-only CoW booking commercialization. All independent release gates OFF.
# The trust provider is a server-side module object with externally persisted
# location, consumed OTP, customer assent, driver completion and legal evidence.
COW_BOOKING_TRUST_PROVIDER = os.environ.get('COW_BOOKING_TRUST_PROVIDER','')
COW_BOOKING_COMMERCIAL_ACCEPT_ENABLED = _cow_hal_strict_flag('COW_BOOKING_COMMERCIAL_ACCEPT_ENABLED')
COW_BOOKING_COMMERCIAL_START_ENABLED = _cow_hal_strict_flag('COW_BOOKING_COMMERCIAL_START_ENABLED')
COW_BOOKING_COMMERCIAL_SETTLEMENT_ENABLED = _cow_hal_strict_flag('COW_BOOKING_COMMERCIAL_SETTLEMENT_ENABLED')
COW_HAL_V1_FINANCIAL_POSTING_ENABLED = _cow_hal_strict_flag('COW_HAL_V1_FINANCIAL_POSTING_ENABLED')
if (COW_BOOKING_COMMERCIAL_ACCEPT_ENABLED or COW_BOOKING_COMMERCIAL_START_ENABLED or
    COW_BOOKING_COMMERCIAL_SETTLEMENT_ENABLED) and not COW_BOOKING_TRUST_PROVIDER:
    raise ValueError('Booking acceptance/charging/settlement requires an approved trust provider')
if COW_BOOKING_COMMERCIAL_START_ENABLED and not COW_HAL_V1_NEW_STARTS_ENABLED:
    raise ValueError('Booking charging starts require the dedicated Go HAL V1 start gate')
if COW_BOOKING_COMMERCIAL_SETTLEMENT_ENABLED and not COW_HAL_V1_FINANCIAL_POSTING_ENABLED:
    raise ValueError('Booking settlement requires a separate financial posting release gate')


# CoW V3: maps-free operations, deliberately independent feature gates.
# Provider adapters remain optional: use NONE for geometry/OTP/tracking core.
COW_MAPS_ROUTING_PROVIDER = os.environ.get('COW_MAPS_ROUTING_PROVIDER', 'NONE')
COW_LOCATION_MODE = os.environ.get('COW_LOCATION_MODE', 'DISABLED')
if COW_LOCATION_MODE not in {'DISABLED','DEVICE_GPS_WITH_OTP','PROVIDER_VERIFIED'}:
    raise ValueError('Unknown COW_LOCATION_MODE')
COW_LOCATION_RECEIVE_ENABLED = _cow_hal_strict_flag('COW_LOCATION_RECEIVE_ENABLED')
COW_HANDOVER_OTP_ENABLED = _cow_hal_strict_flag('COW_HANDOVER_OTP_ENABLED')
COW_DRIVER_WORKFLOW_ENABLED = _cow_hal_strict_flag('COW_DRIVER_WORKFLOW_ENABLED')
COW_ADMIN_CLOSURE_APPROVAL_ENABLED = _cow_hal_strict_flag('COW_ADMIN_CLOSURE_APPROVAL_ENABLED')
COW_WALLET_REFUND_POST_ENABLED = _cow_hal_strict_flag('COW_WALLET_REFUND_POST_ENABLED')
COW_HANDOVER_HMAC_KEY = os.environ.get('COW_HANDOVER_HMAC_KEY', '')
COW_ARRIVAL_RADIUS_M = checked_int(os.environ,'COW_ARRIVAL_RADIUS_M',100,25,500)
COW_LOCATION_FRESH_SECONDS = checked_int(os.environ,'COW_LOCATION_FRESH_SECONDS',90,15,120)
COW_LOCATION_MAX_ACCURACY_M = checked_int(os.environ,'COW_LOCATION_MAX_ACCURACY_M',50,5,200)
COW_BOOKING_SESSION_CEILING_WH = checked_int(os.environ,'COW_BOOKING_SESSION_CEILING_WH',100000,100,500000)
# This buffer is a POLICY decision; acceptance cannot enable on the zero default.
COW_BOOKING_PROTECTED_BUFFER_PAISE = checked_int(os.environ,'COW_BOOKING_PROTECTED_BUFFER_PAISE',0,0,10000000)
if COW_MAPS_ROUTING_PROVIDER != 'NONE' and (not isinstance(COW_MAPS_ROUTING_PROVIDER,str) or '.' not in COW_MAPS_ROUTING_PROVIDER):
    raise ValueError('Routing provider must be NONE or a dotted, approved adapter path')
if COW_HANDOVER_OTP_ENABLED:
    if COW_LOCATION_MODE != 'DEVICE_GPS_WITH_OTP' or not COW_LOCATION_RECEIVE_ENABLED:
        raise ValueError('Handover OTP requires independent maps-free location ingestion')
    if len(COW_HANDOVER_HMAC_KEY) < 32:
        raise ValueError('Handover OTP requires server-only 32+ character HMAC secret')
if COW_BOOKING_TRUST_PROVIDER == 'cow.services.booking_trust_nomap.provider':
    if COW_LOCATION_MODE != 'DEVICE_GPS_WITH_OTP':
        raise ValueError('No-map trust provider requires DEVICE_GPS_WITH_OTP mode')
    if (COW_BOOKING_COMMERCIAL_ACCEPT_ENABLED or COW_BOOKING_COMMERCIAL_START_ENABLED):
        if COW_BOOKING_PROTECTED_BUFFER_PAISE <= 0 or not COW_HANDOVER_OTP_ENABLED:
            raise ValueError('No-map charging requires approved positive buffer and OTP workflow')
if COW_ADMIN_CLOSURE_APPROVAL_ENABLED and not COW_DRIVER_WORKFLOW_ENABLED:
    raise ValueError('Admin closure approval requires the driver workflow release gate')
if COW_WALLET_REFUND_POST_ENABLED and not COW_HAL_V1_FINANCIAL_POSTING_ENABLED:
    raise ValueError('Wallet refunds require a separate financial-posting release gate')
