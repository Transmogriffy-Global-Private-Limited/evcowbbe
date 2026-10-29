"""Explicit PASSWORD_PEPPER for Django Argon2; stable and purpose separated.

Existing Go password hashes are not re-used after the approved DB reset.
"""
import hashlib
import hmac
from django.conf import settings
from django.contrib.auth.hashers import Argon2PasswordHasher


def pepper(password: str) -> str:
    # HMAC the UTF-8 password before Argon2. Constant key never leaves memory.
    return hmac.new(settings.PASSWORD_PEPPER.encode(), b"cow-pwd-v1\0" + password.encode(), hashlib.sha256).hexdigest()


class PepperedArgon2PasswordHasher(Argon2PasswordHasher):
    algorithm = "cow_argon2id_peppered_v1"

    def encode(self, password, salt):
        return super().encode(pepper(password), salt)

    def verify(self, password, encoded):
        return super().verify(pepper(password), encoded)

    def must_update(self, encoded):
        return super().must_update(encoded)

    def __init__(self):
        super().__init__()
        # Django constructs the Parameters object from these instance fields.
        self.memory_cost = settings.PASSWORD_ARGON2_MEMORY_KIB
        self.time_cost = settings.PASSWORD_ARGON2_ITERATIONS
        self.parallelism = settings.PASSWORD_ARGON2_PARALLELISM
