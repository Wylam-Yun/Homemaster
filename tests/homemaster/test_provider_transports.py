from __future__ import annotations

from homemaster.providers.transports.anthropic import AnthropicTransport


def test_anthropic_stream_prefers_input_json_delta_over_empty_start_input() -> None:
    events = [
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "call_1",
                "name": "task_interpreter",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "input_json_delta",
                "partial_json": '{"utterance":"请检查桌子附近有没有药盒"}',
            },
        },
        {"type": "content_block_stop", "index": 0},
    ]

    deltas = list(AnthropicTransport().iter_stream_deltas(events))

    assert len(deltas) == 1
    assert deltas[0].tool_call_delta is not None
    assert deltas[0].tool_call_delta.name == "task_interpreter"
    assert deltas[0].tool_call_delta.arguments == {
        "utterance": "请检查桌子附近有没有药盒"
    }
