"""Approval routes: per-session pending list plus the protocol-2 decision surface."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import ValidationError

from homemaster.permissions.models import (
    ApprovalConflict,
    ApprovalExpired,
    ApprovalSubmission,
    ItemDecision,
    PermissionStorageUnavailable,
)
from homemaster.web._shared import (
    _WEB_PERMISSION_SUBJECT,
    _approval_to_dict,
    _error,
    _resolution_to_dict,
    _session_ids,
)
from homemaster.web.context import WebAppContext
from homemaster.web.schemas import ApprovalSubmissionRequest, CancelApprovalRequest


def register(app: FastAPI, ctx: WebAppContext) -> None:
    application = ctx.application
    confirmation_handler = ctx.confirmation_handler
    _web_store = ctx.web_store

    @app.get("/api/sessions/{session_id}/approvals")
    async def list_session_approvals(session_id: str) -> object:
        if session_id not in _session_ids(application.session_manager):
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        store = _web_store()
        if store is None:
            return _error(
                503,
                "permission_store_unavailable",
                "The permission store is unavailable.",
                retryable=True,
            )
        approvals: list[dict[str, Any]] = []
        for approval_id in await confirmation_handler.pending_approval_ids(session_id):
            try:
                approvals.append(_approval_to_dict(store.get_request(approval_id)))
            except KeyError:
                continue
        return {"approvals": approvals}

    @app.post("/api/approvals/{approval_id}")
    async def resolve_approval(approval_id: str, body: dict) -> object:
        try:
            submission_request = ApprovalSubmissionRequest.model_validate(body)
        except ValidationError:
            if isinstance(body, dict) and "outcome" in body:
                return _error(
                    422,
                    "approval_protocol_outdated",
                    "This console speaks approval protocol 2 with per-item"
                    " decisions; refresh the page and decide again.",
                    retryable=False,
                )
            return _error(
                422,
                "invalid_request",
                "An approval submission must carry protocol_version,"
                " submission_id, request_revision and decisions.",
                retryable=False,
            )
        try:
            submission = ApprovalSubmission(
                protocol_version=submission_request.protocol_version,
                submission_id=submission_request.submission_id,
                request_revision=submission_request.request_revision,
                decisions=tuple(
                    ItemDecision(item_id=item.item_id, choice=item.choice)
                    for item in submission_request.decisions
                ),
            )
        except ValidationError:
            return _error(
                422,
                "invalid_request",
                "Each decision needs an item_id and one of allow_once,"
                " allow_always, allow_session or reject.",
                retryable=False,
            )
        store = _web_store()
        if store is None:
            return _error(
                503,
                "permission_store_unavailable",
                "The permission store is unavailable.",
                retryable=True,
            )
        try:
            resolution = await confirmation_handler.resolve(
                approval_id, submission, _WEB_PERMISSION_SUBJECT.subject_id
            )
        except KeyError:
            return _error(
                404,
                "approval_not_found",
                "The approval is unknown, expired, or already resolved.",
                retryable=False,
            )
        except ApprovalConflict:
            return _error(
                409,
                "approval_conflict",
                "The approval changed; reload it and decide again.",
                retryable=False,
            )
        except ApprovalExpired:
            return _error(
                410,
                "approval_expired",
                "The approval passed its deadline without a decision.",
                retryable=False,
            )
        except ValueError:
            return _error(
                422,
                "invalid_request",
                "The submission must decide exactly the pending items once each.",
                retryable=False,
            )
        except PermissionStorageUnavailable:
            return _error(
                503,
                "permission_store_unavailable",
                "The decision could not be saved; nothing was executed.",
                retryable=True,
            )
        return _resolution_to_dict(resolution)

    @app.get("/api/approvals/{approval_id}")
    async def read_approval(approval_id: str) -> object:
        store = _web_store()
        if store is None:
            return _error(
                503,
                "permission_store_unavailable",
                "The permission store is unavailable.",
                retryable=True,
            )
        try:
            stored = store.get_request(approval_id)
        except KeyError:
            return _error(
                404,
                "approval_not_found",
                "The approval is unknown, expired, or already resolved.",
                retryable=False,
            )
        return _approval_to_dict(stored)

    @app.post("/api/approvals/{approval_id}/cancel")
    async def cancel_approval(approval_id: str, body: CancelApprovalRequest) -> object:
        if _web_store() is None:
            return _error(
                503,
                "permission_store_unavailable",
                "The permission store is unavailable.",
                retryable=True,
            )
        try:
            status = await confirmation_handler.cancel_approval(
                approval_id, body.submission_id, body.request_revision
            )
        except KeyError:
            return _error(
                404,
                "approval_not_found",
                "The approval is unknown, expired, or already resolved.",
                retryable=False,
            )
        except ApprovalConflict:
            return _error(
                409,
                "approval_conflict",
                "The approval changed; reload it and decide again.",
                retryable=False,
            )
        except PermissionStorageUnavailable:
            return _error(
                503,
                "permission_store_unavailable",
                "The cancellation could not be saved.",
                retryable=True,
            )
        return {"approval_id": approval_id, "request_status": status}


__all__ = ["register"]
