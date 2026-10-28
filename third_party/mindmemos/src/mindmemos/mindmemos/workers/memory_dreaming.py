"""Kafka worker for offline memory consolidation tasks."""

from __future__ import annotations

from typing import Protocol

from ..config import get_config
from ..infra.kafka import ConsumedMessage
from ..logging import get_logger
from ..pipelines import create_pipeline
from ..pipelines.memory_db import suppress_recording_errors
from ..provider_bindings import provider_config_context
from ..services import dreaming_operation_result, get_operation_ledger
from ..typing import DreamingPipelineInput, MemoryRequestContext

TOPIC = "memory.dreaming"
GROUP_ID = "memory-dreaming-worker"

logger = get_logger(__name__)


class DreamingWorkerPipeline(Protocol):
    async def dream_sync(self, inp: DreamingPipelineInput, context: MemoryRequestContext): ...


async def handle_memory_dreaming(msg: ConsumedMessage) -> None:
    """Consume a queued dreaming task and execute consolidation synchronously."""

    body = msg.json()
    context = MemoryRequestContext.model_validate(body["context"])
    payload = DreamingPipelineInput.model_validate(body.get("input") or {})
    config_context = await provider_config_context(context)
    with config_context:
        pipeline = create_pipeline(type="dreaming", name=get_config().pipelines.dreaming)
        if not hasattr(pipeline, "dream_sync"):
            raise TypeError("configured dreaming pipeline must expose dream_sync for Kafka worker execution")

        logger.info(
            "processing async memory dreaming",
            request_id=context.request_id,
            account_id=context.account_id,
            project_id=context.project_id,
            topic=msg.topic,
            offset=msg.offset,
        )
        try:
            result = await pipeline.dream_sync(payload, context)
        except Exception as exc:
            if payload.operation_id:
                await suppress_recording_errors(
                    get_operation_ledger().fail(context.project_id, payload.operation_id, error=str(exc)),
                    operation="dreaming",
                )
            raise
        if payload.operation_id:
            dreaming_record_id = result.dreaming_record_id or payload.dreaming_record_id or ""
            data_status = result.data_status or "completed"
            await suppress_recording_errors(
                get_operation_ledger().complete(
                    context.project_id,
                    payload.operation_id,
                    status=data_status,
                    result=dreaming_operation_result(
                        payload.operation_id,
                        dreaming_record_id,
                        produced_memory_ids=list(result.produced_memory_ids or []),
                        actions=list(result.actions or []),
                        status=data_status,
                    ),
                ),
                operation="dreaming",
            )
