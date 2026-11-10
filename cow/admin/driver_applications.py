"""Admin recruitment review. Domain is shared; no duplicated Driver state logic."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from django.http import FileResponse
from ninja import Query, Schema
from pydantic import ConfigDict, Field

from cow.services import driver_recruitment as domain
from cow.services import recruitment_wire as wire
from cow.drivers.application import ApplicationView, DocumentView, HistoryEntry, PolicyView


class Strict(Schema):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Reason(Strict):
    reason: str = Field(min_length=8, max_length=500)


class DocumentDecision(Reason):
    decision: Literal["ACCEPTED", "REJECTED"]


class NewPolicy(Reason):
    required_document_types: list[str] = Field(min_length=1, max_length=12)
    scan_required: bool = True
    legal_decision_ref: str = Field(min_length=8, max_length=200)


class Page(Strict):
    items: list[ApplicationView]
    next_cursor: UUID | None


class PageEnvelope(Strict):
    data: Page


class ApplicationEnvelope(Strict):
    data: ApplicationView


class Detail(Strict):
    application: ApplicationView
    policy: PolicyView | None
    documents: list[DocumentView]
    history: list[HistoryEntry]


class DetailEnvelope(Strict):
    data: Detail


class DocumentReviewResult(Strict):
    document: DocumentView
    application_version: int


class DocumentReviewEnvelope(Strict):
    data: DocumentReviewResult


class PolicyEnvelope(Strict):
    data: PolicyView


class PolicyListEnvelope(Strict):
    data: list[PolicyView]


TRANSITIONS = {
    "start-review": "UNDER_REVIEW",
    "request-action": "ACTION_REQUIRED",
    "approve": "APPROVED",
    "reject": "REJECTED",
    "begin-onboarding": "ONBOARDING",
    "complete-onboarding": "ACTIVE",
}


def register(router):
    @router.get("/driver-applications", operation_id="adminDriverApplicationsList", response=PageEnvelope)
    def list_applications(request, limit: int = Query(50, ge=1, le=100),
                          before: UUID | None = None,
                          state: Literal["DRAFT", "SUBMITTED", "UNDER_REVIEW", "ACTION_REQUIRED", "APPROVED",
                                         "REJECTED", "ONBOARDING", "ACTIVE", "WITHDRAWN"] | None = None):
        return {"data": domain.list_applications(request.auth, state=state, before=before, limit=limit)}

    @router.get("/driver-applications/{application_id}", operation_id="adminDriverApplicationGet",
                response=DetailEnvelope)
    def get_application(request, application_id: UUID):
        return {"data": domain.get_application(request.auth, application_id)}

    @router.get("/driver-application-policies", operation_id="adminDriverPoliciesList",
                response=PolicyListEnvelope)
    def policies(request):
        return {"data": domain.list_policies(request.auth)}

    @router.post("/driver-application-policies", operation_id="adminDriverPolicyCreate",
                 response={201: PolicyEnvelope, 200: PolicyEnvelope})
    def policy_create(request, payload: NewPolicy):
        return wire.mutate(request, "admin:driver-policy:create", payload,
            lambda: domain.create_policy(request.auth, payload.required_document_types,
                payload.scan_required, payload.legal_decision_ref, payload.reason), status=201)

    @router.post("/driver-application-policies/{policy_id}/activate", operation_id="adminDriverPolicyActivate",
                 response={200: PolicyEnvelope})
    def policy_activate(request, policy_id: UUID, payload: Reason):
        return wire.mutate(request, f"admin:driver-policy:{policy_id}:activate", payload,
            lambda: domain.activate_policy(request.auth, policy_id, payload.reason))

    # Each operation has an explicit name and capability. No arbitrary PATCH state.
    def _add_transition(path, state):
        @router.post(f"/driver-applications/{{application_id}}/{path}",
                     operation_id="adminDriverApplication" + "".join(x.title() for x in path.split("-")),
                     response={200: ApplicationEnvelope})
        def transition(request, application_id: UUID, payload: Reason):
            expected = wire.version(request)
            return wire.mutate(request, f"admin:driver-application:{application_id}:{path}", payload,
                lambda: domain.admin_transition(request.auth, application_id, expected, state, payload.reason),
                expected_version=expected)
    for path, state in TRANSITIONS.items():
        _add_transition(path, state)

    @router.post("/driver-applications/{application_id}/documents/{document_id}/review",
                 operation_id="adminDriverDocumentReview", response={200: DocumentReviewEnvelope})
    def review_document(request, application_id: UUID, document_id: UUID, payload: DocumentDecision):
        expected = wire.version(request)
        return wire.mutate(request, f"admin:driver-document:{application_id}:{document_id}:review", payload,
            lambda: domain.review_document(request.auth, application_id, document_id,
                    expected, payload.decision, payload.reason), expected_version=expected)

    @router.get("/driver-applications/{application_id}/documents/{document_id}/download",
                operation_id="adminDriverDocumentDownload")
    def download(request, application_id: UUID, document_id: UUID):
        stream, document = domain.document_for_download(request.auth, document_id, application_id=application_id)
        extension = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}.get(document.content_type, ".bin")
        response = FileResponse(stream, as_attachment=True,
                                filename=f"document-{document.pk}{extension}",
                                content_type="application/octet-stream")
        response["X-Document-SHA256"] = document.sha256
        return response
