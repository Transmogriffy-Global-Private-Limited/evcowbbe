"""Read the existing EVCOWBBE environment without changing or exposing its values.

Pure Python, independently testable before Django or PostgreSQL is installed.
A domain-separated key derivation avoids adding a second secrets file while
preserving the existing PASSWORD_PEPPER and OTP_HMAC_PEPPER values.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import parse_qs, unquote, urlsplit


class ConfigError(ValueError):
    pass


def required(env: dict[str, str], key: str, minimum: int = 1) -> str:
    value = env.get(key, "")
    if len(value) < minimum or value.startswith("REPLACE_"):
        raise ConfigError(f"Missing or invalid {key} (value intentionally withheld)")
    return value


def derive_key(env: dict[str, str], purpose: str, *, urlsafe: bool = False) -> str:
    """Derive purpose-isolated keys from the *existing* protected secrets.

    This is not a password KDF: secrets must already be high-entropy random
    values (as expected by the original Go deployment). Same inputs are stable
    across restarts. Never print the resulting key, commit it, or rotate peppers
    without planning session and encrypted-outbox consequences.
    """
    a = required(env, "PASSWORD_PEPPER", 32).encode()
    b = required(env, "OTP_HMAC_PEPPER", 32).encode()
    # Two-step extract + expand with fixed public labels, to isolate subkeys.
    prk = hmac.new(a, b, hashlib.sha256).digest()
    raw = hmac.new(prk, b"evcowbbe-python/v1/" + purpose.encode() + b"\x01", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode() if urlsafe else raw.hex()


def postgres(env: dict[str, str]) -> dict:
    raw = required(env, "DATABASE_URL")
    u = urlsplit(raw)
    if u.scheme not in {"postgresql", "postgres"} or not u.hostname or not u.path.strip("/"):
        raise ConfigError("DATABASE_URL must be a PostgreSQL URL")
    q = parse_qs(u.query, keep_blank_values=True)
    allowed = {"sslmode"}
    if set(q) - allowed:
        raise ConfigError("DATABASE_URL query contains unsupported options")
    sslmode = q.get("sslmode", ["prefer"])[0]
    if sslmode not in {"disable", "prefer", "require", "verify-ca", "verify-full"}:
        raise ConfigError("Invalid DATABASE_URL sslmode")
    if u.hostname not in {"127.0.0.1", "::1", "localhost"} and sslmode in {"disable", "prefer"}:
        raise ConfigError("Remote database must use verified or required TLS")
    name = unquote(u.path.lstrip("/"))
    if name.lower() in {"postgres", "template0", "template1"} or "/" in name:
        raise ConfigError("Reserved database target")
    return {
        "ENGINE": "django.db.backends.postgresql", "NAME": name,
        "USER": unquote(u.username or ""), "PASSWORD": unquote(u.password or ""),
        "HOST": u.hostname, "PORT": u.port or 5432, "CONN_MAX_AGE": 30,
        "OPTIONS": {"connect_timeout": 4, "application_name": "evcowbbe_python", "sslmode": sslmode},
    }


def public_host(env: dict[str, str]) -> tuple[str, list[str]]:
    pub = urlsplit(required(env, "PUBLIC_BASE_URL"))
    mode = env.get("APP_ENV", "development")
    if pub.scheme not in {"http", "https"} or not pub.hostname or pub.username or pub.password:
        raise ConfigError("PUBLIC_BASE_URL invalid")
    if mode != "development" and mode != "test" and pub.scheme != "https":
        raise ConfigError("Nondevelopment PUBLIC_BASE_URL requires HTTPS")
    return pub.hostname, sorted({pub.hostname, "127.0.0.1", "localhost", "testserver"})


def listen(env: dict[str, str]) -> str:
    host, sep, port = required(env, "HTTP_LISTEN_ADDR").rpartition(":")
    if sep != ":" or host not in {"127.0.0.1", "localhost"} or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ConfigError("HTTP_LISTEN_ADDR must be loopback:port")
    return f"{host}:{int(port)}"


def smtp_mode(env: dict[str, str]) -> str:
    mode = env.get("SMTP_TLS_MODE", "").lower()
    if mode not in {"tls", "starttls"}:
        raise ConfigError("SMTP_TLS_MODE must be tls or starttls")
    return mode


def checked_int(env: dict[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    s = env.get(name, str(default))
    if not s.isdecimal():
        raise ConfigError(f"{name} must be a positive integer")
    n = int(s)
    if not minimum <= n <= maximum:
        raise ConfigError(f"{name} outside supported range")
    return n
