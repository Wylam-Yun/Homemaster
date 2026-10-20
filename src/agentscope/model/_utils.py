# -*- coding: utf-8 -*-
"""Internal utilities for the model module."""
import base64
import json
import re
from collections.abc import Mapping
from typing import Any, Self, TypeAlias

from pydantic import Field

from ._model_response import ChatResponse, FinishedReason
from ._model_usage import ChatUsage
from .._logging import logger
from ..message import (
    Base64Source,
    DataBlock,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    URLSource,
)


class _AccTextBlock(TextBlock):
    """The accumulating counterpart of ``TextBlock``, whose ``text`` field
    holds the incremental fragments until they are joined in ``build``."""

    text: list[str] = Field(  # type: ignore[assignment]
        default_factory=list,
    )

    def append(self, block: TextBlock) -> None:
        """Collect one delta block in constant time.

        Args:
            block (`TextBlock`):
                The delta block to collect.
        """
        self.text.append(block.text)

    def build(self) -> TextBlock:
        """Join the fragments into a plain ``TextBlock``."""
        return TextBlock(**{**self.model_dump(), "text": "".join(self.text)})


class _AccThinkingBlock(ThinkingBlock):
    """The accumulating counterpart of ``ThinkingBlock``, whose ``thinking``
    field holds the incremental fragments until they are joined in
    ``build``."""

    thinking: list[str] = Field(  # type: ignore[assignment]
        default_factory=list,
    )

    def append(self, block: ThinkingBlock) -> None:
        """Collect one delta block in constant time.

        Args:
            block (`ThinkingBlock`):
                The delta block to collect.
        """
        self.thinking.append(block.thinking)
        # Provider-specific extras (e.g. Anthropic's ``signature``) are
        # often only emitted on the closing delta of the block.
        for key, value in (block.model_extra or {}).items():
            if value is not None:
                setattr(self, key, value)

    def build(self) -> ThinkingBlock:
        """Join the fragments into a plain ``ThinkingBlock``."""
        return ThinkingBlock(
            **{**self.model_dump(), "thinking": "".join(self.thinking)},
        )


class _AccToolCallBlock(ToolCallBlock):
    """The accumulating counterpart of ``ToolCallBlock``, whose ``input``
    field holds the incremental JSON fragments until they are joined in
    ``build``."""

    input: list[str] = Field(  # type: ignore[assignment]
        default_factory=list,
    )

    def append(self, block: ToolCallBlock) -> None:
        """Collect one delta block in constant time.

        Args:
            block (`ToolCallBlock`):
                The delta block to collect.
        """
        self.input.append(block.input)
        # Most providers carry ``name`` on the opening delta only, but some
        # send an empty one first and fill the name in afterwards.
        if not self.name and block.name:
            self.name = block.name

    def build(self) -> ToolCallBlock:
        """Join the fragments into a plain ``ToolCallBlock``."""
        return ToolCallBlock(
            **{**self.model_dump(), "input": "".join(self.input)},
        )


class _AccBase64Source(Base64Source):
    """The accumulating counterpart of ``Base64Source`` for audio streams,
    whose ``data`` field holds the raw decoded bytes of each delta.

    Base64 strings cannot be concatenated directly: a delta whose byte
    length is not a multiple of three is padded with ``=``, and everything
    after that padding is silently dropped on decoding. So each delta is
    decoded on arrival and the bytes are encoded once in ``build``.
    """

    data: list[bytes] = Field(  # type: ignore[assignment]
        default_factory=list,
    )

    def append(self, source: Base64Source) -> None:
        """Decode one delta source and collect its raw bytes.

        Args:
            source (`Base64Source`):
                The delta source to collect.
        """
        if source.data:
            self.data.append(base64.b64decode(source.data))

    def build(self) -> Base64Source:
        """Join the raw bytes and base64-encode them once."""
        return Base64Source(
            data=base64.b64encode(b"".join(self.data)).decode("ascii"),
            media_type=self.media_type,
        )


class _AccDataBlock(DataBlock):
    """The accumulating counterpart of ``DataBlock``.

    Only ``audio/*`` is a streamable delta whose concatenation is
    well-defined. Other media types and URL sources are standalone assets,
    so the latest delta replaces the previous one.
    """

    source: (  # type: ignore[assignment]
        _AccBase64Source | Base64Source | URLSource
    )

    def append(self, block: DataBlock) -> None:
        """Collect one delta block in constant time.

        Args:
            block (`DataBlock`):
                The delta block to collect.
        """
        if block.name is not None:
            self.name = block.name

        source = block.source
        if (
            isinstance(self.source, _AccBase64Source)
            and isinstance(source, Base64Source)
            and source.media_type == self.source.media_type
        ):
            self.source.append(source)

        elif isinstance(source, Base64Source) and source.media_type.startswith(
            "audio/",
        ):
            # A new audio stream starts here, either because this is the
            # first delta or because the media type changed mid-stream.
            self.source = _AccBase64Source(
                data=[],
                media_type=source.media_type,
            )
            self.source.append(source)

        else:
            # Standalone asset: the latest delta replaces the previous one
            self.source = source

    def build(self) -> DataBlock:
        """Join the fragments into a plain ``DataBlock``."""
        source = self.source
        return DataBlock(
            **{
                **self.model_dump(),
                "source": (
                    source.build()
                    if isinstance(source, _AccBase64Source)
                    else source
                ),
            },
        )


_AccBlock: TypeAlias = (
    _AccTextBlock | _AccThinkingBlock | _AccToolCallBlock | _AccDataBlock
)


class _StreamAccumulator:
    """Accumulates the streaming ``ChatResponse`` deltas of one model call.

    Each block keeps its deltas in a fragment list that is joined exactly
    once in ``build``, which makes the accumulation O(n) in the total
    payload size. The ``ChatResponse.append_chat_response`` it replaces
    grows the blocks with ``block.field += delta`` instead, which is
    O(n^2) for large payloads (e.g. tool call arguments of 100k+ chars)
    and can block the event loop long enough to drop the connection.
    """

    def __init__(self) -> None:
        """Initialize an empty accumulator."""
        # Keyed by block id, the insertion order is the block order
        self._blocks: dict[str, _AccBlock] = {}

        self.id: str | None = None
        """The id of the latest delta, if any."""

        self.usage: ChatUsage | None = None
        """The usage of the latest delta that reported one, if any."""

        self.finished_reason: FinishedReason = FinishedReason.COMPLETED
        """The finished reason to report in ``build``."""

        # HOMEMASTER PATCH: collect chunk-level metadata (e.g. provider
        # stop_reason captured by the anthropic/openai stream parsers) so
        # ``build`` can expose it on the final response.
        self.metadata: dict = {}

    def append_chat_response(self, chat_response: ChatResponse) -> Self:
        """Collect one delta chunk in constant time per block.

        Args:
            chat_response (`ChatResponse`):
                The streaming delta chunk to collect.
        """
        for block in chat_response.content:
            acc = self._blocks.get(block.id)
            if acc is not None and acc.type != block.type:
                logger.warning(
                    "Block %s changed its type from %s to %s during "
                    "streaming, dropping the accumulated fragments.",
                    block.id,
                    acc.type,
                    block.type,
                )
                acc = None

            if acc is None:
                # Seed the accumulator with every field of the delta, so
                # that the ones it does not accumulate are carried over.
                fields = block.model_dump()
                if isinstance(block, TextBlock):
                    acc = _AccTextBlock(**{**fields, "text": []})
                elif isinstance(block, ThinkingBlock):
                    acc = _AccThinkingBlock(**{**fields, "thinking": []})
                elif isinstance(block, ToolCallBlock):
                    acc = _AccToolCallBlock(**{**fields, "input": []})
                else:
                    # ``source`` is not emptied here, ``append`` replaces
                    # it with a byte accumulator for audio streams.
                    acc = _AccDataBlock(**fields)
                self._blocks[block.id] = acc

            # The type check above guarantees the accumulator and the
            # delta block are of the same kind.
            acc.append(block)  # type: ignore[arg-type]

        if chat_response.usage:
            self.usage = chat_response.usage

        if chat_response.metadata:
            self.metadata.update(chat_response.metadata)

        return self

    def build(self) -> ChatResponse:
        """Join the fragments of every block into a final response."""
        kwargs: dict[str, Any] = {}
        if self.id is not None:
            kwargs["id"] = self.id
        if self.metadata:
            kwargs["metadata"] = dict(self.metadata)

        return ChatResponse(
            content=[_.build() for _ in self._blocks.values()],
            is_last=True,
            usage=self.usage,
            finished_reason=self.finished_reason,
            **kwargs,
        )


# =====================================================================
# HOMEMASTER PATCH (see src/agentscope/VENDORED_SOURCE.md):
# text-channel ``<tool_call>`` rescue. MiMo/Hermes-style models sometimes
# write the real call as ``<tool_call><function=name><parameter=k>v…``
# markup in the reply text while the structured ``tool_use`` block streams
# an empty ``input`` payload. Downstream schema validation then reports
# "missing required arguments" for a call the model believes it sent —
# and retries reproduce the identical leak because the error describes an
# empty string the model never wrote. ``_rescue_leaked_tool_call_inputs``
# backfills an empty/failed structured input from the text-channel markup
# at the one seam every provider's final ``ChatResponse`` crosses
# (``ChatModelBase.__call__``). Rescue only backfills: it never creates a
# tool call the model did not emit through the structured channel, never
# overwrites a structured input that already carries arguments, and is
# keyed by tool name in document order. Unmatched leaked calls are logged
# and recorded in ``response.metadata`` but not synthesized into calls —
# fabricating a ``tool_use`` id the provider never issued is a different
# (larger) semantic change left out deliberately.
# =====================================================================

_TOOL_CALL_SEGMENT_RE = re.compile(
    r"<tool_call>(.*?)</tool_call>",
    re.DOTALL,
)
_FUNCTION_OPEN_RE = re.compile(r"<function\s*=\s*([A-Za-z0-9_.\-]+)\s*>")
_FUNCTION_CLOSE_RE = re.compile(r"</function>\s*$")
_PARAMETER_RE = re.compile(
    r"<parameter\s*=\s*([A-Za-z0-9_.\-]+)\s*>(.*?)</parameter>",
    re.DOTALL,
)


def _extract_leaked_tool_calls(text: str) -> dict[str, list[dict]]:
    """Extract ``<tool_call>`` calls leaked into reply text, keyed by name.

    Two payload shapes are recognized: Hermes-style
    ``<tool_call><function=name><parameter=k>v</parameter>…</tool_call>``
    and the JSON-body form
    ``<tool_call>{"name": "…", "arguments": {…}}</tool_call>``.
    Parameters extracted from the markup stay raw strings; downstream
    argument validation/coercion owns typing.
    """
    leaked: dict[str, list[dict]] = {}
    for segment in _TOOL_CALL_SEGMENT_RE.findall(text):
        segment = segment.strip()
        name: Any = None
        args: dict = {}
        match = _FUNCTION_OPEN_RE.match(segment)
        if match is not None:
            name = match.group(1)
            body = _FUNCTION_CLOSE_RE.sub("", segment[match.end() :])
            args = dict(_PARAMETER_RE.findall(body))
        elif segment.startswith("{"):
            try:
                payload = json.loads(segment)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            name = payload.get("name")
            raw_args = payload.get("arguments")
            args = dict(raw_args) if isinstance(raw_args, dict) else {}
        if not isinstance(name, str) or not name:
            continue
        leaked.setdefault(name, []).append(args)
    return leaked


def _tool_call_input_is_empty(raw: object) -> bool:
    """True when a ``ToolCallBlock.input`` carries no usable arguments:
    missing, blank, unparseable, or decoding to anything but a non-empty
    dict. A ``"{}"`` input counts as empty here only because the rescue
    additionally requires a leaked call for the same name carrying
    non-empty parameters — legitimately argument-free calls are never
    matched by such markup and stay untouched.
    """
    if raw is None:
        return True
    if isinstance(raw, dict):
        return not raw
    if not isinstance(raw, str) or not raw.strip():
        return True
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return True
    return not isinstance(parsed, dict) or not parsed


def _schema_properties_by_tool_name(tools: list[dict] | None) -> dict[str, dict]:
    """Map tool name -> ``{param_name: property schema}`` from either wire
    shape (OpenAI ``{"function": {"parameters": …}}`` or Anthropic-style
    ``{"name", "input_schema"|"parameters"}``)."""
    props_by_tool: dict[str, dict] = {}
    for tool in tools or []:
        if not isinstance(tool, Mapping):
            continue
        name = tool.get("name")
        params = tool.get("parameters") or tool.get("input_schema")
        if name is None and isinstance(tool.get("function"), Mapping):
            fn = tool["function"]
            name = fn.get("name")
            params = fn.get("parameters") or fn.get("input_schema")
        # Tool schemas arrive as frozen ``Mapping`` (contracts freeze), not
        # plain dicts — accept both.
        if isinstance(name, str) and isinstance(params, Mapping):
            props = params.get("properties")
            if isinstance(props, Mapping):
                props_by_tool[name] = props
    return props_by_tool


_INTEGER_RE = re.compile(r"[+-]?\d+")
_NUMBER_RE = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")


def _coerce_xml_value(value: object, prop_schema: object) -> object:
    """Coerce one raw ``<parameter>`` string toward the declared JSON
    Schema type. Values are always extracted as strings; a declared
    ``string`` (alone or in a union) is never rewritten — text stays text.
    """
    if not isinstance(value, str) or not isinstance(prop_schema, Mapping):
        return value
    declared = prop_schema.get("type")
    types = (
        {declared}
        if isinstance(declared, str)
        else set(declared)
        if isinstance(declared, list)
        else set()
    )
    if "string" in types or not types:
        return value
    text = value.strip()
    low = text.lower()
    if "boolean" in types and low in ("true", "false"):
        return low == "true"
    if "null" in types and low in ("null", "none"):
        return None
    if "integer" in types and _INTEGER_RE.fullmatch(text):
        return int(text)
    if "number" in types and _NUMBER_RE.fullmatch(text):
        return float(text)
    if types & {"object", "array"}:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return value
    return value


def _rescue_leaked_tool_call_inputs(
    response: ChatResponse,
    tools: list[dict] | None = None,
) -> ChatResponse:
    """Backfill structured tool-call inputs from leaked ``<tool_call>``
    markup in the same response's text blocks.

    Only applies to a complete response (``is_last`` chunk or non-stream
    reply): delta chunks carry fragments, not parseable calls. Returns the
    response unchanged when nothing was rescued.
    """
    blocks = list(response.content or [])
    if not any(getattr(b, "type", "") == "tool_call" for b in blocks):
        return response
    text = "".join(
        b.text
        for b in blocks
        if getattr(b, "type", "") == "text"
        and isinstance(getattr(b, "text", None), str)
    )
    if "<tool_call" not in text:
        return response
    leaked = _extract_leaked_tool_calls(text)
    if not leaked:
        return response

    # ``<parameter>`` values are always strings; coerce them toward the
    # declared JSON-schema types so dispatch-time validation does not
    # reject a rescued "limit"="60" the model meant as an integer.
    props_by_tool = _schema_properties_by_tool_name(tools)

    rescued: list[str] = []
    unmatched = {name: len(calls) for name, calls in leaked.items()}
    for block in blocks:
        if getattr(block, "type", "") != "tool_call":
            continue
        queue = leaked.get(block.name)
        if not queue:
            continue
        # Every structured call consumes the first leaked call of the same
        # name (document order): a valid structured input keeps its own
        # arguments and merely pairs off the echoed markup, while an empty
        # or unparseable input is backfilled from it.
        args = queue.pop(0)
        unmatched[block.name] -= 1
        if not _tool_call_input_is_empty(block.input) or not args:
            continue
        props = props_by_tool.get(block.name) or {}
        coerced = {
            key: _coerce_xml_value(val, props.get(key))
            for key, val in args.items()
        }
        block.input = json.dumps(coerced, ensure_ascii=False)
        rescued.append(block.name)
    if not rescued and not any(unmatched.values()):
        return response
    if rescued:
        logger.warning(
            "Rescued %d tool call(s) whose structured input was empty while "
            "the call was written as <tool_call> markup in the reply text "
            "(model %s): %s",
            len(rescued),
            getattr(response, "id", "?"),
            ", ".join(rescued),
        )
    unmatched_names = sorted(n for n, c in unmatched.items() if c)
    if unmatched_names:
        logger.warning(
            "Reply text contained <tool_call> markup without a matching "
            "structured tool call (not executed): %s",
            ", ".join(unmatched_names),
        )
    if rescued or unmatched_names:
        if not isinstance(response.metadata, dict):
            response.metadata = {}
        response.metadata["leaked_tool_call_rescue"] = {
            "rescued": rescued,
            "unmatched": unmatched_names,
        }
    return response
