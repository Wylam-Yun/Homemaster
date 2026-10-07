"""Canonical message conversion: HomeMaster <-> AgentScope.

Implements plan/V3.7/decision-message-matrix.md. Structural difference:
AgentScope packs a whole reply (text/thinking/tool_call/tool_result) into one
assistant ``Msg``; HomeMaster models them as separate messages. Conversion is
grouped (H->A) and split (A->H), not 1:1.

Private HomeMaster semantics ride in ``metadata["hm"]`` side-pockets; AS block
ids/timestamps regenerate on H->A and are exempt from round-trip equality.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from agentscope.message import (
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolCallState,
    ToolResultBlock,
    ToolResultState,
    URLSource,
    Usage,
)
from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    Message,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from homemaster.tools.contracts import thaw_json

log = logging.getLogger(__name__)

_ASSISTANT_NAME = "homemaster"
_USER_NAME = "user"


class MessageConversionError(ValueError):
    """Raised when a message cannot be converted without loss or ambiguity."""


# ---------------------------------------------------------------------------
# Block-level helpers
# ---------------------------------------------------------------------------


def _block_to_as(block: ContentBlock) -> list:
    """Convert one HM ContentBlock into AS blocks."""
    if block.type == "text":
        out: list = [TextBlock(text=block.text)]
    elif block.type == "image":
        source = block.source or {}
        source_type = source.get("type")
        if source_type == "base64":
            src = Base64Source(
                data=str(source.get("data", "")),
                media_type=str(source.get("media_type", "image/png")),
            )
        elif source_type == "url":
            src = URLSource(
                url=str(source.get("url", "")),
                media_type=str(source.get("media_type", "image/png")),
            )
        else:
            raise MessageConversionError(f"unsupported image source type: {source_type!r}")
        name = None
        path = block.metadata.get("path")
        if isinstance(path, str) and path:
            name = path.rsplit("/", 1)[-1]
        out = [DataBlock(source=src, name=name)]
    else:
        raise MessageConversionError(f"unsupported content block type: {block.type!r}")
    return out


def _block_from_as(block) -> ContentBlock:
    if isinstance(block, TextBlock):
        return ContentBlock(type="text", text=block.text)
    if isinstance(block, DataBlock):
        src = block.source
        if isinstance(src, Base64Source):
            if not src.media_type.startswith("image/"):
                raise MessageConversionError(
                    f"unsupported data media_type for HM image block: {src.media_type!r}"
                )
            source = {"type": "base64", "media_type": src.media_type, "data": src.data}
        elif isinstance(src, URLSource):
            source = {
                "type": "url",
                "media_type": src.media_type,
                "url": str(src.url),
            }
        else:
            raise MessageConversionError(f"unsupported data source: {type(src)!r}")
        # DataBlock.name is a derived display field (basename of the original
        # path); it must not flow back into HM metadata or round-trips gain a
        # phantom "name" key.
        return ContentBlock(type="image", source=source, metadata={})
    raise MessageConversionError(f"unsupported AS block type: {type(block).__name__!r}")


def _block_from_as_tolerant(block) -> ContentBlock:
    """Convert one text/data block, substituting a marker for content the
    canonical model cannot represent.

    A single unconvertible provider block (e.g. a non-image DataBlock) must
    not abort the whole session mirror — the raw block stays in the engine
    context and snapshot ``agentscope_state``; the canonical face records a
    placeholder instead.
    """
    try:
        return _block_from_as(block)
    except MessageConversionError as exc:
        log.warning("substituting placeholder for unconvertible block: %s", exc)
        return ContentBlock(
            type="text",
            text="[unconvertible provider content omitted]",
            metadata={"hm_unconvertible": type(block).__name__},
        )


def _restore_block_meta(meta: dict, converted: list[ContentBlock]) -> None:
    block_meta = meta.get("block_meta")
    if not isinstance(block_meta, dict):
        return
    for index_str, extra in block_meta.items():
        try:
            index = int(index_str)
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(converted) and isinstance(extra, dict):
            converted[index].metadata.update(extra)


# ---------------------------------------------------------------------------
# H -> A
# ---------------------------------------------------------------------------


def to_agent_scope(messages: Sequence[Message]) -> list[Msg]:
    """Convert canonical HM messages into AS ``Msg`` list (grouped)."""
    out: list[Msg] = []
    current_reply: Msg | None = None
    for message in messages:
        if isinstance(message, UserMessage):
            blocks = [b for i, blk in enumerate(message.content) for b in _block_to_as(blk)]
            metadata: dict = {}
            if any(b.metadata for b in message.content):
                metadata["hm"] = {
                    "block_meta": {
                        str(i): b.metadata for i, b in enumerate(message.content) if b.metadata
                    }
                }
            out.append(Msg(role="user", name=_USER_NAME, content=blocks, metadata=metadata))
            current_reply = None
        elif isinstance(message, AssistantMessage):
            blocks: list = []
            if message.reasoning_content:
                blocks.append(ThinkingBlock(thinking=message.reasoning_content))
            for blk in message.content:
                blocks.extend(_block_to_as(blk))
            for call in message.tool_calls:
                blocks.append(
                    ToolCallBlock(
                        id=call.id,
                        name=call.name,
                        input=json.dumps(call.arguments, ensure_ascii=False, sort_keys=True),
                        state=ToolCallState.PENDING,
                    )
                )
            # One HM segment per merged AssistantMessage: per-message fields
            # survive the reply-level merge losslessly. ``n`` = blocks this
            # segment contributed; 0 marks an originally-empty message.
            seg: dict = {"n": len(blocks)}
            if message.finish_reason is not None:
                seg["finish_reason"] = message.finish_reason
            if message.provider_metadata:
                seg["provider_metadata"] = dict(message.provider_metadata)
            if message.usage:
                seg["usage"] = dict(message.usage)
            if any(b.metadata for b in message.content):
                seg["block_meta"] = {
                    str(i): b.metadata for i, b in enumerate(message.content) if b.metadata
                }
            seg_usage = None
            if message.usage:
                seg_usage = Usage(
                    input_tokens=int(message.usage.get("input_tokens", 0)),
                    output_tokens=int(message.usage.get("output_tokens", 0)),
                    cache_input_tokens=int(message.usage.get("cache_input_tokens", 0)),
                    cache_creation_input_tokens=int(
                        message.usage.get("cache_creation_input_tokens", 0)
                    ),
                )
            if current_reply is not None:
                current_reply.content.extend(blocks)
                hm_md = current_reply.metadata.setdefault("hm", {})
                hm_md.setdefault("segments", []).append(seg)
                if seg_usage is not None:
                    if current_reply.usage is None:
                        current_reply.usage = seg_usage
                    else:
                        current_reply.usage.input_tokens += seg_usage.input_tokens
                        current_reply.usage.output_tokens += seg_usage.output_tokens
                        current_reply.usage.cache_input_tokens += seg_usage.cache_input_tokens
                        current_reply.usage.cache_creation_input_tokens += (
                            seg_usage.cache_creation_input_tokens
                        )
            else:
                msg = Msg(
                    role="assistant",
                    name=_ASSISTANT_NAME,
                    content=blocks,
                    metadata={"hm": {"segments": [seg]}},
                    usage=seg_usage,
                )
                out.append(msg)
                current_reply = msg
        elif isinstance(message, ToolResultMessage):
            if current_reply is None:
                raise MessageConversionError(
                    f"tool result {message.tool_call_id!r} has no assistant message"
                )
            output: list = []
            for blk in message.content:
                output.extend(_block_to_as(blk))
            metadata = {"hm": {}}
            if any(b.metadata for b in message.content):
                metadata["hm"]["block_meta"] = {
                    str(i): b.metadata for i, b in enumerate(message.content) if b.metadata
                }
            if message.data:
                metadata["hm"]["data"] = thaw_json(message.data)
            if message.provider_metadata:
                metadata["hm"]["provider_metadata"] = dict(message.provider_metadata)
            current_reply.content.append(
                ToolResultBlock(
                    id=message.tool_call_id,
                    name=message.name,
                    output=output,
                    state=(ToolResultState.ERROR if message.is_error else ToolResultState.SUCCESS),
                    metadata=metadata,
                )
            )
            for block in current_reply.content:
                if isinstance(block, ToolCallBlock) and block.id == message.tool_call_id:
                    block.state = ToolCallState.FINISHED
        else:
            raise MessageConversionError(f"unsupported message: {type(message)!r}")
    return out


# ---------------------------------------------------------------------------
# A -> H
# ---------------------------------------------------------------------------


def from_agent_scope(messages: Sequence[Msg]) -> list[Message]:
    """Convert AS ``Msg`` list into canonical HM messages (split)."""
    out: list[Message] = []
    for msg in messages:
        hm = msg.metadata.get("hm", {}) if isinstance(msg.metadata, dict) else {}
        if msg.role == "system":
            raise MessageConversionError("system role Msg cannot enter canonical history")
        if msg.role == "user":
            converted = [
                _block_from_as_tolerant(b)
                for b in msg.content
                if isinstance(b, (TextBlock, DataBlock))
            ]
            skipped = len(msg.content) - len(converted)
            if skipped:
                log.warning("dropped %d non text/data blocks from user Msg", skipped)
            _restore_block_meta(hm, converted)
            out.append(UserMessage(content=converted))
        elif msg.role == "assistant":
            _assistant_from_as(msg, hm, out)
        else:
            raise MessageConversionError(f"unsupported Msg role: {msg.role!r}")
    return out


def _assistant_from_as(msg: Msg, hm: dict[str, Any], out: list[Message]) -> None:
    """Split one assistant ``Msg`` back into per-segment HM messages.

    AS accumulates an entire reply round (text/thinking/tool_calls/tool_results)
    into one ``Msg``; HM models each model call as its own ``AssistantMessage``.
    The ``hm["segments"]`` side-pocket written by ``to_agent_scope`` records how
    many blocks each original segment contributed (``n``) plus its
    finish_reason/usage/provider_metadata, so the split is positionally exact —
    including originally-empty segments (``n == 0``).
    """
    segs = hm.get("segments")
    if isinstance(segs, list):
        seg_list = [s if isinstance(s, dict) else {} for s in segs]
    else:
        seg = dict(hm)
        if msg.usage is not None:
            usage = {
                "input_tokens": msg.usage.input_tokens,
                "output_tokens": msg.usage.output_tokens,
            }
            if msg.usage.cache_input_tokens:
                usage["cache_input_tokens"] = msg.usage.cache_input_tokens
            if msg.usage.cache_creation_input_tokens:
                usage["cache_creation_input_tokens"] = msg.usage.cache_creation_input_tokens
            usage.update(hm.get("usage_extra") or {})
            seg["usage"] = usage
        seg_list = [seg]
    seg_idx = 0
    pending_content: list[ContentBlock] = []
    pending_reasoning: list[str] = []
    pending_calls: list[ToolCall] = []

    def emit_empty_segs() -> None:
        # Segments whose "n" is 0 were originally-empty AssistantMessages;
        # they emit positionally before the next segment's blocks.
        nonlocal seg_idx
        while (
            not (pending_content or pending_reasoning or pending_calls)
            and seg_idx < len(seg_list)
            and seg_list[seg_idx].get("n", 1) == 0
        ):
            seg = seg_list[seg_idx]
            seg_idx += 1
            out.append(
                AssistantMessage(
                    finish_reason=seg.get("finish_reason"),
                    usage=seg.get("usage"),
                    provider_metadata=seg.get("provider_metadata") or {},
                )
            )

    def flush() -> None:
        nonlocal seg_idx, pending_content, pending_reasoning, pending_calls
        emit_empty_segs()
        if not (pending_content or pending_reasoning or pending_calls):
            return
        seg = seg_list[seg_idx] if seg_idx < len(seg_list) else {}
        seg_idx += 1
        _restore_block_meta(seg, pending_content)
        out.append(
            AssistantMessage(
                content=pending_content,
                reasoning_content=("\n".join(pending_reasoning) if pending_reasoning else None),
                tool_calls=pending_calls,
                finish_reason=seg.get("finish_reason"),
                usage=seg.get("usage"),
                provider_metadata=seg.get("provider_metadata") or {},
            )
        )
        pending_content, pending_reasoning, pending_calls = [], [], []

    for block in msg.content:
        if isinstance(block, ThinkingBlock):
            if block.thinking:
                pending_reasoning.append(block.thinking)
        elif isinstance(block, (TextBlock, DataBlock)):
            pending_content.append(_block_from_as_tolerant(block))
        elif isinstance(block, ToolCallBlock):
            try:
                arguments = json.loads(block.input or "{}")
            except ValueError:
                arguments = None
            if not isinstance(arguments, dict):
                # A malformed provider frame cannot be represented in the
                # canonical session mirror. Keep the call identity (id/name)
                # and an empty arguments object — its paired result block
                # already records the failure — so the mirror stays
                # consistent instead of poisoning every subsequent sync.
                log.warning(
                    "tool_call %r carried malformed JSON arguments; "
                    "mirroring with empty arguments",
                    block.id,
                )
                arguments = {}
            pending_calls.append(ToolCall(id=block.id, name=block.name, arguments=arguments))
        elif isinstance(block, ToolResultBlock):
            flush()
            result_meta = block.metadata.get("hm", {}) if isinstance(block.metadata, dict) else {}
            output_blocks: list[ContentBlock] = []
            if isinstance(block.output, str):
                if block.output:
                    output_blocks.append(ContentBlock(type="text", text=block.output))
            else:
                output_blocks = [
                    _block_from_as_tolerant(b)
                    for b in block.output
                    if isinstance(b, (TextBlock, DataBlock))
                ]
                _restore_block_meta(result_meta, output_blocks)
            # Protocol-fence denials are completed protocol results, not
            # tool errors — legacy carried status="protocol_blocked" with
            # is_error=False so error guards never trip on them.
            result_data = result_meta.get("data")
            protocol_blocked = (
                block.state == ToolResultState.DENIED
                and isinstance(result_data, dict)
                and result_data.get("status") == "protocol_blocked"
            )
            out.append(
                ToolResultMessage(
                    tool_call_id=block.id,
                    name=block.name,
                    content=output_blocks,
                    is_error=(block.state != ToolResultState.SUCCESS and not protocol_blocked),
                    data=result_data,
                    provider_metadata=result_meta.get("provider_metadata") or {},
                )
            )
        else:
            # HintBlock and any future AS-internal block: engine-side
            # runtime-state annotations regenerated per iteration, not
            # canonical content — drop at DEBUG so steady-state logs
            # stay clean while the projection loss stays traceable.
            log.debug(
                "dropped non-canonical block %s from assistant Msg",
                type(block).__name__,
            )
    flush()
    emit_empty_segs()
    if seg_idx < len(seg_list):
        log.warning(
            "assistant Msg has %d unconsumed segment records",
            len(seg_list) - seg_idx,
        )


# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------


def assert_semantic_equal(a: Msg, b: Msg) -> None:
    """Field-level equality for A->H->A checks; exempt regenerated fields."""
    assert a.role == b.role, f"role {a.role!r} != {b.role!r}"
    assert a.name == b.name, f"name {a.name!r} != {b.name!r}"
    assert len(a.content) == len(b.content), f"block count {len(a.content)} != {len(b.content)}"
    for ba, bb in zip(a.content, b.content, strict=True):
        assert type(ba) is type(bb), f"{type(ba)} != {type(bb)}"
        da = ba.model_dump(exclude={"id", "created_at", "finished_at"})
        db = bb.model_dump(exclude={"id", "created_at", "finished_at"})
        if isinstance(ba, ToolCallBlock):
            da["input"] = json.loads(da["input"] or "{}")
            db["input"] = json.loads(db["input"] or "{}")
            # state is pairing-derived (result present -> FINISHED), exempt
            da.pop("state", None)
            db.pop("state", None)
            da.pop("suggested_rules", None)
            db.pop("suggested_rules", None)
        if isinstance(ba, ToolResultBlock):
            # HM expresses only is_error; DENIED/INTERRUPTED collapse to ERROR
            da["state"] = "ok" if da["state"] == ToolResultState.SUCCESS else "err"
            db["state"] = "ok" if db["state"] == ToolResultState.SUCCESS else "err"


__all__ = [
    "MessageConversionError",
    "assert_semantic_equal",
    "from_agent_scope",
    "to_agent_scope",
]
