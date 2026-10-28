"""SDK client tests for the V3.8 dedicated skill trace + evolution-batch API."""

from __future__ import annotations

import json
from typing import Any

import httpx
from mindmemos_sdk.skills import (
    EvolutionBatchResult,
    SkillCloudClient,
    SkillTrace,
    SkillTraceStep,
    SkillTraceSubmitData,
)
from mindmemos_sdk.transport import HttpTransport


def _response_for(path: str) -> httpx.Response:
    if path.endswith("/traces"):
        data = {"invocation_id": "inv-1", "cloud_skill_id": "skill-1", "status": "sealed", "unconsumed_count": 3}
    elif "/traces/" in path:
        data = {
            "invocation_id": path.rsplit("/", 1)[-1],
            "cloud_skill_id": "skill-1",
            "skill_version_id": "v1",
            "skill_content_hash": "sha256:abc",
            "task_input": "do it",
            "tool_steps": [{"tool_call_id": "c1", "tool_name": "read", "arguments": {"p": "/tmp"}}],
            "agent_completion": "done",
            "outcome": "success",
            "score": 0.9,
            "status": "sealed",
        }
    elif path.endswith("/evolution-batches"):
        data = {"batch_id": "batch-1", "draft_version_id": "v2", "status": "completed", "reason": None}
    else:
        data = None
    return httpx.Response(200, json={"code": "ok", "message": "done", "request_id": "req-1", "data": data})


def _client(captured: list[dict[str, Any]]) -> SkillCloudClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {"method": request.method, "path": request.url.path, "body": json.loads(request.content or b"{}")}
        )
        return _response_for(request.url.path)

    return SkillCloudClient(
        HttpTransport(
            base_url="https://api.test", api_key="mk_test", client=httpx.Client(transport=httpx.MockTransport(handler))
        )
    )


class TestSubmitTrace:
    def test_submit_trace_posts_sealed_trace(self) -> None:
        captured: list[dict[str, Any]] = []
        result = _client(captured).submit_trace(
            "skill-1",
            SkillTrace(
                invocation_id="inv-1",
                skill_version_id="v1",
                skill_content_hash="sha256:abc",
                task_input="do it",
                tool_steps=[SkillTraceStep(tool_call_id="c1", tool_name="read", arguments={"p": "/tmp"})],
                agent_completion="done",
                outcome="success",
                score=0.9,
            ),
        )
        assert captured[0]["method"] == "POST"
        assert captured[0]["path"] == "/v1/skills/skill-1/traces"
        body = captured[0]["body"]
        assert body["invocation_id"] == "inv-1"
        assert body["skill_version_id"] == "v1"
        assert body["tool_steps"][0]["tool_name"] == "read"
        # Response-only fields are never re-sent.
        for forbidden in ("cloud_skill_id", "status", "consumed_by_batch_id", "created_at"):
            assert forbidden not in body
        assert isinstance(result, SkillTraceSubmitData)
        assert result.status == "sealed"
        assert result.unconsumed_count == 3

    def test_submit_trace_accepts_plain_dict(self) -> None:
        captured: list[dict[str, Any]] = []
        _client(captured).submit_trace(
            "skill-1",
            {
                "invocation_id": "inv-1",
                "skill_version_id": "v1",
                "skill_content_hash": "sha256:abc",
                "outcome": "failure",
                "score": 0.1,
            },
        )
        assert captured[0]["body"]["outcome"] == "failure"

    def test_submit_trace_strips_response_fields_when_resubmitting_fetched_model(self) -> None:
        captured: list[dict[str, Any]] = []
        client = _client(captured)
        fetched = client.get_trace("skill-1", "inv-1")
        client.submit_trace("skill-1", fetched)
        body = captured[1]["body"]
        for forbidden in ("cloud_skill_id", "status", "consumed_by_batch_id", "created_at"):
            assert forbidden not in body


class TestGetTrace:
    def test_get_trace_issues_get_and_parses(self) -> None:
        captured: list[dict[str, Any]] = []
        trace = _client(captured).get_trace("skill-1", "inv-1")
        assert captured[0]["method"] == "GET"
        assert captured[0]["path"] == "/v1/skills/skill-1/traces/inv-1"
        assert isinstance(trace, SkillTrace)
        assert trace.invocation_id == "inv-1"
        assert trace.cloud_skill_id == "skill-1"
        assert trace.tool_steps[0].tool_name == "read"
        assert trace.score == 0.9


class TestEvolutionBatch:
    def test_evolution_batch_posts_claim(self) -> None:
        captured: list[dict[str, Any]] = []
        result = _client(captured).evolution_batch("skill-1", "batch-1", ["inv-1", "inv-2"])
        assert captured[0]["method"] == "POST"
        assert captured[0]["path"] == "/v1/skills/skill-1/evolution-batches"
        assert captured[0]["body"] == {"batch_id": "batch-1", "trace_ids": ["inv-1", "inv-2"]}
        assert isinstance(result, EvolutionBatchResult)
        assert result.batch_id == "batch-1"
        assert result.draft_version_id == "v2"
        assert result.status == "completed"
