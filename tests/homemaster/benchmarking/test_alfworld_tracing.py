from __future__ import annotations

import json
from pathlib import Path

from homemaster.benchmarking.alfworld.tracing import AlfworldTraceWriter


def test_trace_writer_writes_jsonl_and_redacts_secret_keys(tmp_path: Path) -> None:
    writer = AlfworldTraceWriter(tmp_path / "episode-1")
    writer.write_event({
        "event": "tool_step",
        "observation": "You see apple 1.",
        "api_key": "secret",
        "nested": {"auth_token": "secret", "safe": "ok"},
    })

    payload = json.loads((tmp_path / "episode-1" / "trace.jsonl").read_text().strip())

    assert payload["event"] == "tool_step"
    assert payload["api_key"] == "[REDACTED]"
    assert payload["nested"]["auth_token"] == "[REDACTED]"
    assert payload["nested"]["safe"] == "ok"
