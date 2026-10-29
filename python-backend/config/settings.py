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
COW_OTP_PEPPER = os.environ["OTP_HMAC_PEPPER"]
PASSWORD_PEPPER = os.environ["PASSWORD_PEPPER"]
DEBUG = False
PUBLIC_HOST, ALLOWED_HOSTS = public_host(os.environ)
COW_PUBLIC_APP_URL = os.environ["PUBLIC_BASE_URL"]
HTTP_LISTEN_ADDR = listen(os.environ)
DATABASES = {"default": postgres(os.environ)}
DB_MAX_CONNS = checked_int(os.environ, "DB_MAX_CONNS", 10, 1, 100)
INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth", "ninja", "cow"]
MIDDLEWARE = ["config.middleware.RequestSecurityMiddleware"]
ROOT_URLCONF = "config.urls"
TEMPLATES = []
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
COW_SMTP_HOST = os.environ.get("SMTP_HOST", "")
COW_SMTP_PORT = checked_int(os.environ, "SMTP_PORT", 587, 1, 65535)
COW_SMTP_USER = os.environ.get("SMTP_USERNAME", "")
COW_SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
COW_SMTP_FROM = os.environ.get("MAIL_FROM_ADDRESS", "")
COW_SMTP_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "TransEV")
COW_SMTP_TLS_MODE = smtp_mode(os.environ)
