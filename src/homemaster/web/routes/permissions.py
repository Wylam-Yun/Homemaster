"""Grant management routes: paginated listing and revision-checked revocation."""

from __future__ import annotations

from fastapi import FastAPI

from homemaster.permissions.models import ApprovalConflict, PermissionStorageUnavailable
from homemaster.web._shared import (
    _WEB_PERMISSION_SUBJECT,
    _error,
    _grant_to_dict,
)
from homemaster.web.context import WebAppContext
from homemaster.web.schemas import RevokeGrantRequest


def register(app: FastAPI, ctx: WebAppContext) -> None:
    _web_store = ctx.web_store

    @app.get("/api/permissions/grants")
    async def list_grants(
        resource_kind: str | None = None,
        status: str = "active",
        environment_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> object:
        store = _web_store()
        if store is None:
            return _error(
                503,
                "permission_store_unavailable",
                "The permission store is unavailable.",
                retryable=True,
            )
        if resource_kind is not None and resource_kind not in ("object", "area"):
            return _error(
                422,
                "invalid_request",
                "resource_kind must be object or area.",
                retryable=False,
            )
        try:
            if environment_id:
                grants, next_cursor = store.list_grants(
                    environment_id,
                    resource_kind=resource_kind,
                    status=status,
                    cursor=cursor,
                    limit=limit,
                )
            else:
                grants, next_cursor = store.list_all_grants(
                    resource_kind=resource_kind,
                    status=status,
                    cursor=cursor,
                    limit=limit,
                )
        except KeyError:
            return _error(
                404,
                "grant_cursor_not_found",
                "The page cursor is unknown.",
                retryable=False,
            )
        except ValueError:
            return _error(
                422,
                "invalid_request",
                "status must be active or revoked and limit within 1..200.",
                retryable=False,
            )
        return {
            "grants": [
                _grant_to_dict(grant, store.grant_display(grant.source_item_id)) for grant in grants
            ],
            "next_cursor": next_cursor,
        }

    @app.post("/api/permissions/grants/{grant_id}/revoke")
    async def revoke_grant(grant_id: str, body: RevokeGrantRequest) -> object:
        store = _web_store()
        if store is None:
            return _error(
                503,
                "permission_store_unavailable",
                "The permission store is unavailable.",
                retryable=True,
            )
        try:
            grant = store.revoke(
                grant_id,
                body.submission_id,
                body.expected_revision,
                _WEB_PERMISSION_SUBJECT.subject_id,
            )
        except KeyError:
            return _error(
                404,
                "grant_not_found",
                "The grant is unknown.",
                retryable=False,
            )
        except ApprovalConflict:
            return _error(
                409,
                "grant_conflict",
                "The grant changed; reload the list and revoke again.",
                retryable=False,
            )
        except PermissionStorageUnavailable:
            return _error(
                503,
                "permission_store_unavailable",
                "The revocation could not be saved.",
                retryable=True,
            )
        return _grant_to_dict(grant, store.grant_display(grant.source_item_id))


__all__ = ["register"]
