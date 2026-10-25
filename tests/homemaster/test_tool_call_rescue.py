"""Text-channel ``<tool_call>`` rescue — model-boundary contract tests.

Regression for the observed failure where a MiMo-family model emitted
``tool_use`` blocks with an empty structured ``input`` while writing the
real call as ``<tool_call><function=name><parameter=k>v</parameter>``
markup in the reply text. ``search_files`` failed three times with
``invalid_tool_arguments`` even though the reply text carried complete
parameters; the bare "arguments were ''" error could not explain to the
model what it had actually done.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agentscope._utils._common import _json_loads_with_repair
from agentscope.credential import OpenAICredential
from agentscope.exception import ToolJSONDecodeError
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.model._utils import (
    _coerce_xml_value,
    _extract_leaked_tool_calls,
    _rescue_leaked_tool_call_inputs,
    _schema_properties_by_tool_name,
    _tool_call_input_is_empty,
)


def _xml_call(name: str, **params: Any) -> str:
    inner = "".join(f"<parameter={key}>{value}</parameter>" for key, value in params.items())
    return f"<tool_call><function={name}>{inner}</function></tool_call>"


class _ScriptedStreamModel(ChatModelBase):
    """Plays a fixed list of stream chunks through the real ``__call__``
    accumulation seam (the same path every provider model uses)."""

    def __init__(self, script: list[ChatResponse]) -> None:
        super().__init__(
            credential=OpenAICredential(api_key="sk-stub"),
            model="stub-model",
            parameters=ChatModelBase.Parameters(),
        )
        self._script = list(script)

    async def _call_api(
        self,
        model_name: str,
        messages: list,
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> Any:
        chunks = list(self._script)

        async def _gen():
            for chunk in chunks:
                yield chunk

        return _gen()


class _ScriptedNonStreamModel(ChatModelBase):
    """Returns one complete ChatResponse per call (non-stream path)."""

    def __init__(self, response: ChatResponse) -> None:
        super().__init__(
            credential=OpenAICredential(api_key="sk-stub"),
            model="stub-model",
            parameters=ChatModelBase.Parameters(),
        )
        self._response = response

    async def _call_api(
        self,
        model_name: str,
        messages: list,
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse:
        return self._response


async def _drain(model: ChatModelBase) -> ChatResponse:
    res = await model([])
    assert not isinstance(res, ChatResponse)
    chunks = [chunk async for chunk in res]
    return chunks[-1]


def test_extract_leaked_tool_calls_xml_style() -> None:
    text = (
        "let me search\n"
        + _xml_call("search_files", pattern="def load_skill", path="/repo/src")
        + "\ndone"
    )
    leaked = _extract_leaked_tool_calls(text)
    assert leaked == {
        "search_files": [{"pattern": "def load_skill", "path": "/repo/src"}],
    }


def test_extract_leaked_tool_calls_json_body() -> None:
    text = (
        '<tool_call>{"name": "web_fetch", "arguments": '
        '{"url": "https://example.com", "max_chars": 4000}}</tool_call>'
    )
    leaked = _extract_leaked_tool_calls(text)
    assert leaked == {
        "web_fetch": [{"url": "https://example.com", "max_chars": 4000}],
    }


def test_extract_leaked_tool_calls_ignores_noise() -> None:
    assert _extract_leaked_tool_calls("no markup here") == {}
    assert _extract_leaked_tool_calls("<tool_call>not valid</tool_call>") == {}
    # Malformed JSON body is skipped, not raised.
    assert _extract_leaked_tool_calls("<tool_call>{oops</tool_call>") == {}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", True),
        ("   ", True),
        ("{}", True),
        ("[]", True),
        ('"x"', True),
        ("{not json", True),
        (None, True),
        ('{"pattern": "x"}', False),
    ],
)
def test_tool_call_input_is_empty(raw: Any, expected: bool) -> None:
    assert _tool_call_input_is_empty(raw) is expected


def test_rescue_backfills_empty_input() -> None:
    response = ChatResponse(
        content=[
            TextBlock(text=_xml_call("search_files", pattern="skills")),
            ToolCallBlock(id="t1", name="search_files", input=""),
        ],
        is_last=True,
    )
    out = _rescue_leaked_tool_call_inputs(response)
    block = out.content[1]
    assert isinstance(block, ToolCallBlock)
    assert json.loads(block.input) == {"pattern": "skills"}
    assert out.metadata["leaked_tool_call_rescue"] == {
        "rescued": ["search_files"],
        "unmatched": [],
    }


def test_rescue_never_overwrites_real_arguments() -> None:
    response = ChatResponse(
        content=[
            TextBlock(text=_xml_call("search_files", pattern="from-text")),
            ToolCallBlock(
                id="t1",
                name="search_files",
                input='{"pattern": "from-structured"}',
            ),
        ],
        is_last=True,
    )
    out = _rescue_leaked_tool_call_inputs(response)
    block = out.content[1]
    assert json.loads(block.input) == {"pattern": "from-structured"}
    # The echo pairs off the leaked call — nothing counts as unmatched.
    assert "leaked_tool_call_rescue" not in out.metadata


def test_rescue_leaves_name_mismatch_alone() -> None:
    response = ChatResponse(
        content=[
            TextBlock(text=_xml_call("terminal", command="ls")),
            ToolCallBlock(id="t1", name="search_files", input=""),
        ],
        is_last=True,
    )
    out = _rescue_leaked_tool_call_inputs(response)
    block = out.content[1]
    assert block.input == ""
    assert out.metadata["leaked_tool_call_rescue"]["unmatched"] == ["terminal"]


def test_rescue_pairs_same_name_calls_in_document_order() -> None:
    response = ChatResponse(
        content=[
            TextBlock(
                text=_xml_call("search_files", pattern="first")
                + _xml_call("search_files", pattern="second"),
            ),
            ToolCallBlock(id="t1", name="search_files", input=""),
            ToolCallBlock(id="t2", name="search_files", input=""),
        ],
        is_last=True,
    )
    out = _rescue_leaked_tool_call_inputs(response)
    assert json.loads(out.content[1].input) == {"pattern": "first"}
    assert json.loads(out.content[2].input) == {"pattern": "second"}


@pytest.mark.parametrize(
    ("value", "schema", "expected"),
    [
        ("60", {"type": "integer"}, 60),
        ("-3", {"type": "integer"}, -3),
        ("false", {"type": "boolean"}, False),
        ("true", {"type": "boolean"}, True),
        ("3.5", {"type": "number"}, 3.5),
        ("null", {"type": ["null", "object"]}, None),
        # Declared string (alone or unioned) is never rewritten.
        ("60", {"type": "string"}, "60"),
        ("60", {"type": ["string", "null"]}, "60"),
        ("false", {"type": ["string", "boolean"]}, "false"),
        # No declared type -> raw string passthrough.
        ("60", {}, "60"),
        ("60", None, "60"),
        # Object/array params accept embedded JSON.
        ('{"a": 1}', {"type": "object"}, {"a": 1}),
        ("[1, 2]", {"type": "array"}, [1, 2]),
        ("not json", {"type": "object"}, "not json"),
    ],
)
def test_coerce_xml_value(value: Any, schema: Any, expected: Any) -> None:
    assert _coerce_xml_value(value, schema) == expected


def test_schema_properties_both_wire_shapes() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_files",
                "parameters": {"properties": {"limit": {"type": "integer"}}},
            },
        },
        {
            "name": "read_file",
            "input_schema": {"properties": {"path": {"type": "string"}}},
        },
    ]
    props = _schema_properties_by_tool_name(tools)
    assert props["search_files"]["limit"]["type"] == "integer"
    assert props["read_file"]["path"]["type"] == "string"


def test_schema_properties_accept_frozen_mappings() -> None:
    """HomeMaster freezes tool schemas into ``MappingProxyType`` — the
    lookup must not drop them for failing ``isinstance(x, dict)``."""
    from types import MappingProxyType

    tools = [
        MappingProxyType(
            {
                "type": "function",
                "function": MappingProxyType(
                    {
                        "name": "search_files",
                        "parameters": MappingProxyType(
                            {
                                "properties": MappingProxyType(
                                    {"limit": MappingProxyType({"type": "integer"})}
                                )
                            }
                        ),
                    }
                ),
            }
        )
    ]
    props = _schema_properties_by_tool_name(tools)
    assert props["search_files"]["limit"]["type"] == "integer"


def test_rescue_coerces_xml_scalars_with_schema() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_files",
                "parameters": {
                    "properties": {
                        "pattern": {"type": "string"},
                        "limit": {"type": "integer"},
                        "case_sensitive": {"type": "boolean"},
                    }
                },
            },
        }
    ]
    response = ChatResponse(
        content=[
            TextBlock(
                text=_xml_call(
                    "search_files",
                    pattern="x",
                    limit="60",
                    case_sensitive="false",
                )
            ),
            ToolCallBlock(id="t1", name="search_files", input=""),
        ],
        is_last=True,
    )
    out = _rescue_leaked_tool_call_inputs(response, tools)
    assert json.loads(out.content[1].input) == {
        "pattern": "x",
        "limit": 60,
        "case_sensitive": False,
    }


def test_rescue_no_tool_call_blocks_is_noop() -> None:
    response = ChatResponse(
        content=[TextBlock(text=_xml_call("search_files", pattern="x"))],
        is_last=True,
    )
    out = _rescue_leaked_tool_call_inputs(response)
    assert "leaked_tool_call_rescue" not in (out.metadata or {})


@pytest.mark.asyncio
async def test_call_stream_accumulated_response_is_rescued() -> None:
    """Delta stream with empty tool_use input + XML in text deltas → the
    accumulated final response the toolkit parses carries real args."""
    model = _ScriptedStreamModel(
        [
            ChatResponse(
                content=[
                    TextBlock(
                        id="txt",
                        text="<tool_call><function=search_files>"
                        "<parameter=pattern>loader</parameter>",
                    ),
                ],
                is_last=False,
            ),
            ChatResponse(
                content=[TextBlock(id="txt", text="</tool_call>")],
                is_last=False,
            ),
            ChatResponse(
                content=[
                    ToolCallBlock(id="t1", name="search_files", input=""),
                ],
                is_last=False,
            ),
        ],
    )
    final = await _drain(model)
    assert final.is_last
    block = final.content[-1]
    assert isinstance(block, ToolCallBlock)
    assert json.loads(block.input) == {"pattern": "loader"}
    assert "search_files" in final.metadata["leaked_tool_call_rescue"]["rescued"]


@pytest.mark.asyncio
async def test_call_is_last_chunk_is_rescued() -> None:
    """Providers that emit a complete is_last chunk get the same rescue."""
    model = _ScriptedStreamModel(
        [
            ChatResponse(
                content=[TextBlock(id="txt", text="working...")],
                is_last=False,
            ),
            ChatResponse(
                content=[
                    TextBlock(id="txt", text=_xml_call("terminal", command="ls")),
                    ToolCallBlock(id="t1", name="terminal", input=""),
                ],
                is_last=True,
            ),
        ],
    )
    final = await _drain(model)
    block = final.content[1]
    assert json.loads(block.input) == {"command": "ls"}


@pytest.mark.asyncio
async def test_call_non_stream_response_is_rescued() -> None:
    model = _ScriptedNonStreamModel(
        ChatResponse(
            content=[
                TextBlock(text=_xml_call("read_file", path="/a/b.py")),
                ToolCallBlock(id="t1", name="read_file", input="{}"),
            ],
            is_last=True,
        ),
    )
    res = await model([])
    assert isinstance(res, ChatResponse)
    block = res.content[1]
    assert json.loads(block.input) == {"path": "/a/b.py"}


def test_empty_args_error_names_the_leak() -> None:
    with pytest.raises(ToolJSONDecodeError) as excinfo:
        _json_loads_with_repair("")
    assert "tool-call interface" in str(excinfo.value)
    assert "your_tool_arguments = ''" in str(excinfo.value)
