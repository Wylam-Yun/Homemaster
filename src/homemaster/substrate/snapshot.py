"""Snapshot schema v2 per plan/V3.7/decision-snapshot-schema.md.

Envelope (``SessionSnapshot{revision, generation, environment_ref,
payload}``) is unchanged. ``payload["schema_version"] == 2`` adds
``agentscope_state`` (engine-authoritative ``agentscope.state.AgentState``
dump), ``canonical_evidence_refs`` and ``require_recall`` to the v1 fields.

Authority split:
- ``agentscope_state.context`` is the engine-authoritative context — it is
  the only carrier of ToolCallBlock lifecycle state (ASKING/SUBMITTED).
- ``messages`` is a reader-side projection derived from that context via
  ``from_agent_scope``; it keeps serving export/audit/list readers.
- ``agent_state`` keeps HomeMaster run bookkeeping (no field overlap).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentscope.message import DataBlock, Msg, TextBlock
from agentscope.state import AgentState as EngineState
from homemaster.agent.messages import Message
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState as RunBookkeepingState
from homemaster.substrate.messages import (
    MessageConversionError,
    from_agent_scope,
    to_agent_scope,
)
from homemaster.task_state.store import TaskStateStore

SNAPSHOT_SCHEMA_VERSION = 2


def _is_image_block(block: Any) -> bool:
    return isinstance(block, DataBlock) and block.source.media_type.startswith(
        "image/"
    )


def _image_placeholder(tool_name: str, index: int) -> TextBlock:
    return TextBlock(
        text=(
            f"[image stripped - {tool_name} @ iter {index}, "
            "args={}. See trace.jsonl for original]"
        )
    )


def _strip_context_images(
    context: list[Msg],
    preserve_tool_call_ids: frozenset[str],
) -> list[Msg]:
    """Return deep copies with image DataBlocks replaced by placeholders.

    Tool-result blocks whose ``id`` (== HM ``tool_call_id``) is in
    ``preserve_tool_call_ids`` keep their images, matching the v1
    ``preserve_image_tool_call_ids`` semantics.
    """
    stripped: list[Msg] = []
    for index, msg in enumerate(context):
        msg = msg.model_copy(deep=True)
        msg.content = [
            _image_placeholder(msg.name or msg.role, index)
            if _is_image_block(block)
            else block
            for block in msg.content
        ]
        for block in msg.content:
            if (
                block.type == "tool_result"
                and isinstance(block.output, list)
                and block.id not in preserve_tool_call_ids
            ):
                block.output = [
                    _image_placeholder(block.name, index)
                    if _is_image_block(item)
                    else item
                    for item in block.output
                ]
        stripped.append(msg)
    return stripped


def build_snapshot_payload(
    *,
    engine_state: EngineState,
    run_state: RunBookkeepingState,
    task_state_store: TaskStateStore,
    model: str,
    system_prompt: str,
    created_at: float | None = None,
    canonical_evidence_refs: tuple[str, ...] = (),
    require_recall: bool = False,
    strip_images: bool = True,
    preserve_image_tool_call_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Build a schema-v2 snapshot payload from the live engine state.

    ``messages`` is projected from the (possibly image-stripped) engine
    context so both faces derive from the same source of truth.
    """
    context = list(engine_state.context)
    if strip_images:
        context = _strip_context_images(context, preserve_image_tool_call_ids)
    engine_dump = engine_state.model_dump(mode="json")
    engine_dump["context"] = [m.model_dump(mode="json") for m in context]

    projected = AgentSession(session_id=engine_state.session_id)
    projected.replace_messages(from_agent_scope(context))
    if created_at is not None:
        projected._created_at = float(created_at)
    payload = projected.to_snapshot_dict(
        agent_state=run_state,
        task_state_store=task_state_store,
        model=model,
        system_prompt=system_prompt,
        strip_images=strip_images,
        preserve_image_tool_call_ids=preserve_image_tool_call_ids,
    )
    payload["schema_version"] = SNAPSHOT_SCHEMA_VERSION
    payload["agentscope_state"] = engine_dump
    payload["canonical_evidence_refs"] = list(canonical_evidence_refs)
    payload["require_recall"] = bool(require_recall)
    return payload


@dataclass
class ParsedSnapshot:
    """Result of loading a snapshot payload (v1 or v2)."""

    session_id: str
    created_at: float
    engine_state: EngineState
    messages: list[Message]
    run_state: RunBookkeepingState
    task_state: TaskStateStore
    canonical_evidence_refs: list[str] = field(default_factory=list)
    require_recall: bool = False
    migrated_from_v1: bool = False


def parse_snapshot_payload(payload: dict[str, Any]) -> ParsedSnapshot:
    """Fail-closed load per decision doc: unknown future versions reject."""
    version = int(payload.get("schema_version") or 1)
    if version > SNAPSHOT_SCHEMA_VERSION:
        raise MessageConversionError(
            f"snapshot schema_version {version} > {SNAPSHOT_SCHEMA_VERSION}; "
            "refusing to load a future format"
        )
    session, run_state, task_state = AgentSession.from_snapshot_dict(payload)
    messages = list(session.messages)
    if version == SNAPSHOT_SCHEMA_VERSION and payload.get("agentscope_state"):
        engine_state = EngineState.model_validate(payload["agentscope_state"])
        migrated = False
    else:
        engine_state = EngineState(
            session_id=session.session_id,
            context=to_agent_scope(messages),
        )
        migrated = True
    return ParsedSnapshot(
        session_id=session.session_id,
        created_at=float(payload.get("created_at") or 0.0),
        engine_state=engine_state,
        messages=messages,
        run_state=run_state,
        task_state=task_state,
        canonical_evidence_refs=list(
            payload.get("canonical_evidence_refs") or []
        ),
        require_recall=bool(payload.get("require_recall") or False),
        migrated_from_v1=migrated,
    )


__all__ = [
    "ParsedSnapshot",
    "SNAPSHOT_SCHEMA_VERSION",
    "build_snapshot_payload",
    "parse_snapshot_payload",
]
