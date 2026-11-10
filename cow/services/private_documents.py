"""Private local recruitment-document storage; provider-agnostic contract.

No public URLs or client-selected paths. S3-compatible implementation may replace
LocalPrivateStore behind put/open/stat/delete without changing document identities.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from cow.common.errors import reject

MAX_BYTES = 10 * 1024 * 1024
SIGNATURES = (
    (b"%PDF-", "application/pdf", ".pdf"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
)


@dataclass(frozen=True)
class StoredFile:
    key: str
    size: int
    sha256: str
    content_type: str


def _root() -> Path:
    provider = os.environ.get("STORAGE_PROVIDER", "").upper()
    value = os.environ.get("PRIVATE_STORAGE_ROOT", "")
    if provider != "LOCAL" or not value or not os.path.isabs(value):
        reject(503, "DOCUMENT_STORAGE_NOT_READY", "Private document storage is not configured")
    configured = Path(value)
    if configured.is_symlink():
        reject(503, "DOCUMENT_STORAGE_UNSAFE", "Private storage root cannot be a symlink")
    root = configured.resolve()
    # The private root must never overlap the web application's source/static tree.
    app_root = Path(settings.BASE_DIR).resolve()
    if root == app_root or app_root in root.parents or root in app_root.parents:
        reject(503, "DOCUMENT_STORAGE_NOT_READY", "Private document storage location is unsafe")
    if not root.is_dir() or root.is_symlink():
        reject(503, "DOCUMENT_STORAGE_NOT_READY", "Private document storage directory is unavailable")
    return root


def _path(key: str) -> Path:
    # Server-issued keys are two levels of random lowercase hex, never original names.
    if (len(key) != 65 or key[32] != "/" or
        any(c not in "0123456789abcdef" for c in key[:32] + key[33:])):
        reject(404, "DOCUMENT_NOT_FOUND", "Document not found")
    root = _root()
    child = root / key[:32]
    if child.is_symlink():
        reject(503, "DOCUMENT_STORAGE_UNSAFE", "Private storage integrity check failed")
    return child / key[33:]


def _kind(prefix: bytes):
    for magic, mime, extension in SIGNATURES:
        if prefix.startswith(magic):
            return mime, extension
    reject(422, "DOCUMENT_TYPE_REJECTED", "Only PDF, JPEG and PNG documents are supported")


class LocalPrivateStore:
    """A bounded, single-pass, crash-reconcilable local adapter.

    Files are written with exclusive creation and restrictive permissions.
    Interrupted uploads can leave unreferenced random objects; the separate
    maintenance/retention worker must reconcile before deletion.
    """

    def put(self, uploaded) -> StoredFile:
        root = _root()
        if uploaded.size is not None and uploaded.size > MAX_BYTES:
            reject(413, "DOCUMENT_TOO_LARGE", "Document exceeds the 10 MiB limit")
        prefix = secrets.token_hex(16)
        name = secrets.token_hex(16)
        directory = root / prefix
        directory.mkdir(mode=0o700)
        destination = directory / name
        fd = None
        total = 0
        digest = hashlib.sha256()
        header = b""
        try:
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb", closefd=True) as stream:
                fd = None
                for chunk in uploaded.chunks():
                    total += len(chunk)
                    if total > MAX_BYTES:
                        reject(413, "DOCUMENT_TOO_LARGE", "Document exceeds the 10 MiB limit")
                    if len(header) < 16:
                        header += chunk[:16 - len(header)]
                    digest.update(chunk)
                    stream.write(chunk)
                if total == 0:
                    reject(422, "DOCUMENT_EMPTY", "Empty documents are not accepted")
                mime, _ = _kind(header)
                stream.flush()
                os.fsync(stream.fileno())
            return StoredFile(prefix + "/" + name, total, digest.hexdigest(), mime)
        except BaseException:
            if fd is not None:
                os.close(fd)
            destination.unlink(missing_ok=True)
            try:
                directory.rmdir()
            except OSError:
                pass
            raise

    def open(self, key: str):
        path = _path(key)
        if path.is_symlink() or not path.is_file():
            reject(404, "DOCUMENT_NOT_FOUND", "Document data unavailable")
        return path.open("rb")

    def verify(self, stored: StoredFile) -> None:
        try:
            handle = self.open(stored.key)
        except OSError:
            reject(503, "DOCUMENT_STORAGE_UNAVAILABLE", "Document data unavailable")
        size = 0
        digest = hashlib.sha256()
        with handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                size += len(chunk)
                if size > MAX_BYTES:
                    break
                digest.update(chunk)
        if size != stored.size or digest.hexdigest() != stored.sha256:
            reject(409, "DOCUMENT_INTEGRITY_CONFLICT", "Document integrity verification failed")

    def delete_uncommitted(self, key: str) -> None:
        path = _path(key)
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass


def store():
    """Extension port. Unsupported configured providers fail closed."""
    return LocalPrivateStore()
