"""AgentScope-side provider fakes shared by migrated test files.

A legacy scripted fake transport exposes ``stream() -> AsyncIterator[
TransportDelta]``. ApplicationRuntime's AgentScope-only provider seam expects
``chat_model()`` returning a vendored ``ChatModelBase``. This module adapts the
legacy fakes so their scripts, call recording, blocking and cancellation
internals keep driving the AS path unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from homemaster.providers.types import TransportDelta


def deltas_to_response(deltas: list[TransportDelta]) -> Any:
    """Aggregate a legacy ``TransportDelta`` script into one ``ChatResponse`` —
    the AS-side analogue of the old ``stream()`` generator. Block order
    follows the delta order; ``tool_calls``/``length`` finish reasons map to
    provider-native stop reasons."""
    from agentscope.message import TextBlock, ThinkingBlock, ToolCallBlock
    from agentscope.model import ChatResponse
    from agentscope.model._model_usage import ChatUsage

    blocks: list[Any] = []
    stop_reason = "end_turn"
    for delta in deltas:
        if getattr(delta, "text_delta", None):
            blocks.append(TextBlock(text=delta.text_delta))
        if getattr(delta, "reasoning_delta", None):
            blocks.append(ThinkingBlock(thinking=delta.reasoning_delta))
        if getattr(delta, "tool_call_delta", None):
            call = delta.tool_call_delta
            blocks.append(
                ToolCallBlock(
                    id=call.id,
                    name=call.name,
                    input=json.dumps(dict(call.arguments or {})),
                )
            )
        finish = getattr(delta, "finish_reason", None)
        if finish:
            stop_reason = (
                "tool_use"
                if finish == "tool_calls"
                else "length"
                if finish == "length"
                else "end_turn"
            )
    return ChatResponse(
        content=blocks,
        is_last=True,
        usage=ChatUsage(input_tokens=1, output_tokens=1, time=0.0),
        metadata={"stop_reason": stop_reason},
    )


class DeltaModel:
    """Wrap one legacy ``stream()``-shaped fake transport as a vendored
    ``ChatModelBase``: ``_call_api`` drains the fake's delta stream and
    returns one aggregated ``ChatResponse``. The fake keeps its own
    ``calls`` recording, blocking, cancellation and message-inspection
    internals — only the seam shape changes."""

    def __init__(self, transport: Any) -> None:
        from agentscope.credential import OpenAICredential
        from agentscope.formatter import OpenAIChatFormatter
        from agentscope.model import ChatModelBase

        class _Model(ChatModelBase):
            def __init__(self) -> None:
                super().__init__(
                    credential=OpenAICredential(api_key="sk-stub"),
                    model="stub-model",
                    parameters=ChatModelBase.Parameters(),
                )
                self.formatter = OpenAIChatFormatter()

            async def _call_api(
                self,
                model_name: str,
                messages: list[Any],
                tools: list[dict[str, Any]] | None = None,
                **kwargs: Any,
            ) -> Any:
                del model_name
                gen = transport.stream(messages, tools=tools, **kwargs)
                deltas: list[Any] = []
                try:
                    async for delta in gen:
                        deltas.append(delta)
                finally:
                    await gen.aclose()
                return deltas_to_response(deltas)

        self._model = _Model()

    def __call__(self) -> Any:
        return self._model


def as_provider(value: Any) -> Any:
    """Provider-seam wrapper: exposes ``chat_model()`` (+ ``api_format``,
    ``complete``/``aclose``/``close`` pass-throughs) so ApplicationRuntime's
    AS branch accepts the fake. ``ResourceBinding`` inputs keep their
    ownership — the wrapper is bound around the inner resource."""
    from homemaster.application.resources import ResourceBinding

    if isinstance(value, ResourceBinding):
        release = None
        if value.release is not None:
            original_release = value.release

            def release(resource: Any) -> Any:
                return original_release(getattr(resource, "_transport", resource))

        return ResourceBinding(
            name=value.name,
            resource=as_provider(value.resource),
            ownership=value.ownership,
            lifetime=value.lifetime,
            release=release,
        )
    transport = value
    model = DeltaModel(transport)

    class _Provider:
        api_format = ""

        def chat_model(self, **_kwargs: Any) -> Any:
            return model()

        async def aclose(self) -> None:
            closer = getattr(transport, "aclose", None)
            if closer is not None:
                await closer()
            else:
                closer = getattr(transport, "close", None)
                if closer is not None:
                    closer()

        def close(self) -> None:
            closer = getattr(transport, "close", None)
            if closer is not None:
                closer()

        async def complete(self, *args: Any, **kwargs: Any) -> Any:
            complete = getattr(transport, "complete", None)
            if complete is None:
                raise NotImplementedError("fake transport does not implement complete()")
            return await complete(*args, **kwargs)

    provider = _Provider()
    provider._transport = transport
    return provider


def schema_name(schema: Any) -> str:
    """Tool name out of an AgentScope/OpenAI function schema."""
    function = schema.get("function") if isinstance(schema, dict) else None
    if isinstance(function, dict):
        return str(function.get("name", ""))
    return str(schema.get("name", "")) if isinstance(schema, dict) else ""


def tool_result_blocks(messages: Any, name: str | None = None) -> list[Any]:
    """``ToolResultBlock``s inside the merged assistant ``Msg``s — the AS
    analogue of canonical ``role == "tool"`` messages."""
    blocks: list[Any] = []
    for message in messages:
        for block in getattr(message, "content", ()) or ():
            if getattr(block, "type", None) != "tool_result":
                continue
            if name is not None and getattr(block, "name", None) != name:
                continue
            blocks.append(block)
    return blocks


def result_text(block: Any) -> str:
    """Model-visible text of a ``ToolResultBlock`` (its ``output`` items)."""
    return "\n".join(
        text for item in getattr(block, "output", ()) or () if (text := getattr(item, "text", None))
    )
