"""Feedback and dreaming paths for the embedded MindMemOS runtime."""

from __future__ import annotations

from typing import Any

from homemaster.memory._mindmemos_shared import (
    _FEEDBACK_PROVENANCE_SEQ,
    MEMORY_RECORD_ADAPTER,
    _record_metadata,
    serialize_record,
)


class MindMemOSFeedbackMixin:
    """MindMemOS feedback paths (explicit/implicit feedback, dreaming)."""

    async def _execute_structured_feedback_update(
        self,
        action: Any,
        current: Any,
        context: Any,
    ) -> Any:
        """Apply a feedback replacement through the canonical Schema writer."""

        current_metadata = _record_metadata(getattr(current, "metadata", None))
        try:
            current_record = MEMORY_RECORD_ADAPTER.validate_json(current_metadata["record_json"])
            replacement_record = MEMORY_RECORD_ADAPTER.validate_python(action.replacement_record)
        except (KeyError, TypeError, ValueError):
            return action.model_copy(
                update={"result_memory_id": action.target_memory_id, "status": "error"}
            )
        current_seq = int(current_metadata.get("provenance_seq", 0))
        provenance_seq = _FEEDBACK_PROVENANCE_SEQ.get()
        # The contextvar carries the feedback turn's evidence seq, which can
        # legitimately equal the record's own seq — e.g. a user correcting a
        # memory in the same turn that wrote it. Ordering only requires strict
        # increase, so clamp instead of rejecting.
        if provenance_seq is None or provenance_seq <= current_seq:
            provenance_seq = current_seq + 1
        current_serialized = serialize_record(current_record, provenance_seq=current_seq)
        replacement = serialize_record(replacement_record, provenance_seq=provenance_seq)
        if (
            current_record.memory_type != replacement_record.memory_type
            or current_serialized.dedupe_key != replacement.dedupe_key
            or replacement_record.source != current_record.source
        ):
            return action.model_copy(
                update={"result_memory_id": action.target_memory_id, "status": "error"}
            )
        result = await self.update_versioned(
            memory_id=action.target_memory_id,
            content=replacement.text,
            metadata={
                **replacement.metadata,
                "homemaster_memory_type": replacement_record.memory_type,
            },
            context=context,
        )
        if result.status != "ok" or not isinstance(result.memory_id, str):
            return action.model_copy(
                update={"result_memory_id": action.target_memory_id, "status": "error"}
            )
        return action.model_copy(
            update={
                "result_memory_id": result.memory_id,
                "after_content": replacement.text,
                "replacement_record": replacement_record.model_dump(mode="json"),
                "status": "ok",
            }
        )

    async def feedback_explicit(
        self,
        *,
        feedback: str,
        messages: list[Any],
        recalled_memories: list[Any],
        provenance_seq: int,
        context: Any,
    ) -> Any:
        from mindmemos.typing import FeedbackPipelineInput

        if self._feedback_pipeline is None:
            raise RuntimeError("embedded MindMemOS is not started")
        token = _FEEDBACK_PROVENANCE_SEQ.set(provenance_seq)
        try:
            return await self._feedback_pipeline.feedback_sync(
                FeedbackPipelineInput(
                    feedback=feedback,
                    messages=messages,
                    recalled_memories=recalled_memories,
                    mode="sync",
                ),
                context,
            )
        finally:
            _FEEDBACK_PROVENANCE_SEQ.reset(token)

    async def feedback_implicit(self, context: Any) -> Any:
        from mindmemos.typing import FeedbackPipelineInput

        if self._feedback_pipeline is None:
            raise RuntimeError("embedded MindMemOS is not started")
        return await self._feedback_pipeline.feedback_sync(
            FeedbackPipelineInput(mode="sync"), context
        )

    async def dream(
        self,
        *,
        seed_add_record_ids: list[str],
        context: Any,
    ) -> Any:
        from mindmemos.typing import DreamingPipelineInput

        if self._dreaming_pipeline is None:
            raise RuntimeError("embedded MindMemOS is not started")
        return await self._dreaming_pipeline.dream_sync(
            DreamingPipelineInput(
                mode="sync",
                seed_add_record_ids=seed_add_record_ids,
            ),
            context,
        )
