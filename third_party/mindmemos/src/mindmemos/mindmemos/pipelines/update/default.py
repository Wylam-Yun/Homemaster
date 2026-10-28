"""Default update-memory pipeline implementation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...errors import MemoryNotFoundError
from ...typing import (
    MemoryDbMutationPlan,
    MemoryDbUpdateCommand,
    MemoryRequestContext,
    UpdatePipelineInput,
    UpdatePipelineResult,
)
from ..base import MemoryDbPipelineMixin
from ..registry import register

# Typed top-level memory payload fields addressable via ``fields`` (V3.8).
_STRUCTURED_PAYLOAD_FIELDS = {
    "mem_type",
    "mem_extract_type",
    "mem_extract_version",
    "property_name",
    "entity_id",
    "entity_type",
    "validate_from",
    "validate_to",
    "reinforcement_count",
    "parent_ids",
    "root_id",
}
_UPDATE_FIELD_ALIASES = {"event_time": "validate_from"}
_STRUCTURED_DATETIME_FIELDS = {"validate_from", "validate_to"}


def _split_update_fields(fields: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split ``fields`` into (typed payload patch, metadata patch extras)."""

    payload_patch: dict[str, Any] = {}
    metadata_extras: dict[str, Any] = {}
    for raw_key, value in fields.items():
        key = _UPDATE_FIELD_ALIASES.get(raw_key, raw_key)
        if key in _STRUCTURED_PAYLOAD_FIELDS:
            if key in _STRUCTURED_DATETIME_FIELDS and isinstance(value, str):
                try:
                    value = datetime.fromisoformat(value)
                except ValueError:
                    pass
            payload_patch[key] = value
        else:
            metadata_extras[raw_key] = value
    return payload_patch, metadata_extras


@register(type="update", name="default_update")
class DefaultUpdatePipeline(MemoryDbPipelineMixin):
    """Patch one memory through the project-scoped memory DB writer."""

    async def update(self, inp: UpdatePipelineInput, context: MemoryRequestContext) -> UpdatePipelineResult:
        """Patch the content/typed fields of an active memory.

        Args:
            inp: Update request with target memory id and replacement content.
            context: Tenant, project, and actor context for hard isolation.

        Returns:
            An ok result when the update was applied, otherwise an error result.
        """
        memory = await self.db_reader.get_memory(context, inp.id)
        if memory is None:
            raise MemoryNotFoundError(inp.id)

        payload_patch: dict[str, Any] = {}
        metadata_patch = dict(inp.metadata_patch)
        status = inp.status
        if inp.fields:
            fields_payload, fields_metadata = _split_update_fields(inp.fields)
            payload_patch.update(fields_payload)
            metadata_patch.update(fields_metadata)
            # ``fields.status`` drives the lifecycle path when ``status`` is unset.
            if status is None and inp.fields.get("status") in ("active", "archived"):
                status = inp.fields["status"]

        if memory.status != "active" and status != "active":
            return UpdatePipelineResult(
                status="error",
                message=f"memory is not active (status={memory.status}): {inp.id}",
            )
        if inp.content is None and not metadata_patch and not payload_patch and status is None:
            return UpdatePipelineResult(status="error", message="nothing to update")
        if inp.content is not None and not inp.content.strip():
            return UpdatePipelineResult(status="error", message="content is empty")

        # ``graph_content_sync`` stays False: the writer already syncs graph
        # content whenever ``content`` is present, and forcing it on a
        # fields-only update would push an empty string to the graph node.
        command = MemoryDbUpdateCommand(
            memory_id=inp.id,
            content=inp.content,
            metadata_patch=metadata_patch,
            payload_patch=payload_patch,
            status=status,
        )
        await self.db_writer.apply_mutation_plan(
            context,
            MemoryDbMutationPlan(memory_updates=[command]),
        )
        return UpdatePipelineResult(status="ok", message=None)
