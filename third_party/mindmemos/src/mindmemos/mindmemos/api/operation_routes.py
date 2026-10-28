"""Durable operation-ledger status routes (V3.8).

``GET /v1/operations/{operation_id}`` lets clients reconcile lost responses
before retrying an idempotent lifecycle call (add / feedback / dreaming /
management mutations).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path

from ..errors import ResourceNotFoundError
from ..services import OperationLedger
from .deps import get_operation_ledger, get_request_context
from .schemas import ApiResponse, AuthContext, OperationStatusData

router = APIRouter(prefix="/v1/operations", tags=["operations"])

OperationStatusResponse = ApiResponse[OperationStatusData]


@router.get("/{operation_id}", response_model=OperationStatusResponse)
async def get_operation_status(
    operation_id: str = Path(min_length=1),
    auth: AuthContext = Depends(get_request_context),
    ledger: OperationLedger = Depends(get_operation_ledger),
) -> OperationStatusResponse:
    """Return the durable ledger row for ``operation_id`` (404 when unknown)."""

    record = await ledger.get(auth.project_id, operation_id)
    if record is None:
        raise ResourceNotFoundError("operation not found", code="not_found")
    return OperationStatusResponse(
        code="ok",
        request_id=auth.request_id,
        data=OperationStatusData(
            operation_id=record.operation_id,
            kind=record.kind,
            status=record.status,
            result=record.result,
            error=record.error,
            created_at=record.created_at,
            updated_at=record.updated_at,
        ),
    )
