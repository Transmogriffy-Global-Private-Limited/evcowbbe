"""Driver recruitment HTTP API; submitted IDs are never storage paths."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from django.http import FileResponse
from ninja import File, Form, Schema
from ninja.files import UploadedFile
from pydantic import ConfigDict, Field

from cow.common.errors import reject
from cow.services import driver_recruitment as domain
from cow.services import idempotency
from cow.services import recruitment_wire as wire
from cow.services.private_documents import store


class Strict(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ApplicationView(Strict):
    id: UUID
    driver_id: UUID
    state: str
    version: int
    legal_name: str
    contact_phone: str
    policy_version: int | None
    submitted_at: datetime | None
    created_at: datetime


class DocumentView(Strict):
    id: UUID
    document_type: str
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    expires_on: date | None
    replaces_id: UUID | None
    review_status: str
    scan_status: str
    created_at: datetime


class PolicyView(Strict):
    configured: bool
    required_document_types: list[str]
    scan_required: bool
    version: int | None
    id: UUID | None = None
    active: bool | None = None
    legal_decision_ref: str | None = None


class OwnView(Strict):
    application: ApplicationView | None
    requirements: PolicyView
    documents: list[DocumentView]


class OwnEnvelope(Strict):
    data: OwnView


class ApplicationEnvelope(Strict):
    data: ApplicationView


class UploadView(Strict):
    document: DocumentView
    application_version: int


class UploadEnvelope(Strict):
    data: UploadView


class HistoryEntry(Strict):
    from_state: str
    to_state: str
    reason: str
    created_at: datetime


class HistoryEnvelope(Strict):
    data: list[HistoryEntry]


class EditPayload(Strict):
    legal_name: str = Field(min_length=2, max_length=160)
    contact_phone: str = Field(min_length=7, max_length=32)


class ReasonPayload(Strict):
    reason: str = Field(min_length=8, max_length=500)


class NoBody(Strict):
    pass


def _safe_name(name: str, actual: str) -> str:
    source = str(name or "")
    normalized = source.replace("\\", "/")
    candidate = PurePosixPath(normalized).name
    if candidate != normalized or candidate in {"", ".", ".."} or len(candidate) > 255:
        reject(422, "DOCUMENT_FILENAME_INVALID", "Unsafe document filename")
    if any(ord(c) < 32 for c in candidate):
        reject(422, "DOCUMENT_FILENAME_INVALID", "Unsafe document filename")
    suffix = PurePosixPath(candidate).suffix.lower()
    legal = {
        "application/pdf": {".pdf"},
        "image/jpeg": {".jpg", ".jpeg"},
        "image/png": {".png"},
    }
    if suffix not in legal.get(actual, set()):
        reject(422, "DOCUMENT_EXTENSION_MISMATCH", "Document signature and extension do not agree")
    return candidate


def register(router):
    @router.get("/application", operation_id="driverApplicationGet", response=OwnEnvelope)
    def get_application(request):
        return {"data": domain.read_own(request.auth)}

    @router.put("/application", operation_id="driverApplicationPut", response={200: ApplicationEnvelope})
    def put_application(request, payload: EditPayload):
        version = wire.version(request)
        return wire.mutate(request, "driver:application:put", payload,
            lambda: domain.update_own(request.auth, version, payload.legal_name, payload.contact_phone),
            expected_version=version)

    @router.post("/application/documents", operation_id="driverApplicationDocumentUpload",
                 response={201: UploadEnvelope, 200: UploadEnvelope})
    def upload_document(request, document_type: str = Form(...),
                        file: UploadedFile = File(...), expires_on: date | None = Form(None)):
        if request.content_type.split(";", 1)[0].lower() != "multipart/form-data":
            reject(415, "UNSUPPORTED_MEDIA_TYPE", "Multipart document upload required")
        version = wire.version(request)
        key = wire.key(request)
        staged = store().put(file)
        committed = False
        try:
            name = _safe_name(file.name, staged.content_type)
            if file.content_type not in {staged.content_type, "application/octet-stream"}:
                reject(422, "DOCUMENT_MIME_MISMATCH", "Document MIME declaration does not match file signature")
            metadata = {"document_type": document_type, "expires_on": expires_on.isoformat() if expires_on else None,
                        "filename": name, "sha256": staged.sha256, "size_bytes": staged.size,
                        "_expected_version": version}
            invoked = False
            def persist():
                nonlocal invoked
                result = domain.upload_own(request.auth, version, document_type, name, expires_on, staged)
                invoked = True
                return 201, {"data": result}
            receipt = idempotency.execute(request.auth, "driver:application:document:upload", key,
                                          metadata, persist, retention_hours=168)
            committed = invoked
            return receipt
        finally:
            if not committed:
                store().delete_uncommitted(staged.key)

    @router.post("/application/submit", operation_id="driverApplicationSubmit",
                 response={200: ApplicationEnvelope})
    def submit(request, payload: NoBody):
        version = wire.version(request)
        return wire.mutate(request, "driver:application:submit", payload,
            lambda: domain.submit_own(request.auth, version), expected_version=version)

    @router.post("/application/withdraw", operation_id="driverApplicationWithdraw",
                 response={200: ApplicationEnvelope})
    def withdraw(request, payload: ReasonPayload):
        version = wire.version(request)
        return wire.mutate(request, "driver:application:withdraw", payload,
            lambda: domain.withdraw_own(request.auth, version, payload.reason), expected_version=version)

    @router.get("/application/reviews", operation_id="driverApplicationReviews", response=HistoryEnvelope)
    def history(request):
        return {"data": domain.own_history(request.auth)}

    @router.get("/application/documents/{document_id}/download",
                operation_id="driverApplicationDocumentDownload")
    def download(request, document_id: UUID):
        stream, document = domain.document_for_download(request.auth, document_id)
        extension = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}.get(document.content_type, ".bin")
        response = FileResponse(stream, as_attachment=True,
                                filename=f"document-{document.pk}{extension}",
                                content_type="application/octet-stream")
        response["X-Document-SHA256"] = document.sha256
        return response
