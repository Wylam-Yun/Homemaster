"""Kafka worker for asynchronous memory feedback tasks."""

from __future__ import annotations

from ..config import get_config
from ..infra.kafka import ConsumedMessage
from ..logging import get_logger
from ..pipelines import create_pipeline
from ..pipelines.feedback import MEMORY_FEEDBACK_TOPIC
from ..pipelines.memory_db import suppress_recording_errors
from ..provider_bindings import provider_config_context
from ..services import feedback_operation_result, get_operation_ledger
from ..typing import FeedbackPipelineInput, MemoryRequestContext

TOPIC = MEMORY_FEEDBACK_TOPIC
GROUP_ID = "memory-feedback-worker"

logger = get_logger(__name__)


async def handle_memory_feedback(msg: ConsumedMessage) -> None:
    """Consume a queued feedback task and execute feedback synchronously."""

    body = msg.json()
    context = MemoryRequestContext.model_validate(body["context"])
    payload = FeedbackPipelineInput.model_validate({**(body.get("input") or {}), "mode": "sync"})
    config_context = await provider_config_context(context)
    with config_context:
        pipeline = create_pipeline(type="feedback", name=get_config().pipelines.feedback)
        if not hasattr(pipeline, "feedback_sync"):
            raise TypeError("configured feedback pipeline must expose feedback_sync for Kafka worker execution")

        logger.info(
            "processing async memory feedback",
            request_id=context.request_id,
            account_id=context.account_id,
            project_id=context.project_id,
            topic=msg.topic,
            offset=msg.offset,
        )
        try:
            result = await pipeline.feedback_sync(payload, context)
        except Exception as exc:
            if payload.operation_id:
                await suppress_recording_errors(
                    get_operation_ledger().fail(context.project_id, payload.operation_id, error=str(exc)),
                    operation="feedback",
                )
            raise
        if payload.operation_id:
            feedback_record_id = payload.feedback_record_id or ""
            actions = [
                {
                    "action_id": f"{feedback_record_id}:{index}",
                    "action": getattr(action, "action", "noop"),
                    "memory_id": getattr(action, "result_memory_id", None) or getattr(action, "target_memory_id", None),
                }
                for index, action in enumerate(getattr(result, "actions", None) or [])
            ]
            data_status = "no_action" if not actions else "completed"
            await suppress_recording_errors(
                get_operation_ledger().complete(
                    context.project_id,
                    payload.operation_id,
                    status=data_status,
                    result=feedback_operation_result(
                        payload.operation_id,
                        feedback_record_id,
                        actions=actions,
                        status=data_status,
                    ),
                ),
                operation="feedback",
            )
