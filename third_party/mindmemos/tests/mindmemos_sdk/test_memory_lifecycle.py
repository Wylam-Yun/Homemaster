"""Sync/async SDK parity tests for the V3.8 memory lifecycle surface.

Covers the additive request parameters (``operation_id`` on add/feedback/
dreaming/update/delete, feedback ``window``, dreaming ``seed_add_record_ids``,
update ``fields``) and the new ``GET /v1/operations/{id}`` status lookup —
asserting both the wire body and the parsed response data.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from mindmemos_sdk.memory import (
    AsyncMemoryClient,
    DialogueMessage,
    FeedbackWindow,
    MemoryClient,
    OperationStatus,
)
from mindmemos_sdk.transport import AsyncHttpTransport, HttpTransport


def _response_for(path: str) -> httpx.Response:
    data: dict[str, Any] | None = None
    if path.endswith("/add"):
        data = {
            "operation_id": "op-add",
            "add_record_id": "ar-1",
            "memory_ids": ["m1"],
            "memories": [{"operation": "add", "content": "hello", "memory_id": "m1"}],
            "status": "completed",
            "replayed": False,
        }
    elif path.endswith("/feedback"):
        data = {"operation_id": "op-fb", "feedback_record_id": "fbr-1", "actions": [], "status": "no_action"}
    elif path.endswith("/dreaming"):
        data = {
            "operation_id": "op-dr",
            "dreaming_record_id": "dr-1",
            "produced_memory_ids": [],
            "status": "no_action",
        }
    elif "/operations/" in path:
        data = {
            "operation_id": path.rsplit("/", 1)[-1],
            "kind": "add",
            "status": "completed",
            "result": {"memory_ids": ["m1"]},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:01Z",
        }
    return httpx.Response(200, json={"code": "ok", "message": "done", "request_id": "req-1", "data": data})


def _sync_client(captured: list[dict[str, Any]]) -> MemoryClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {"method": request.method, "path": request.url.path, "body": json.loads(request.content or b"{}")}
        )
        return _response_for(request.url.path)

    return MemoryClient(
        HttpTransport(
            base_url="https://api.test", api_key="mk_test", client=httpx.Client(transport=httpx.MockTransport(handler))
        ),
        default_user_id="u-default",
    )


def _async_client(captured: list[dict[str, Any]]) -> AsyncMemoryClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {"method": request.method, "path": request.url.path, "body": json.loads(request.content or b"{}")}
        )
        return _response_for(request.url.path)

    return AsyncMemoryClient(
        AsyncHttpTransport(
            base_url="https://api.test",
            api_key="mk_test",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ),
        default_user_id="u-default",
    )


class TestSyncLifecycleParams:
    def test_add_sends_operation_id_and_parses_result(self) -> None:
        captured: list[dict[str, Any]] = []
        result = _sync_client(captured).add([DialogueMessage(role="user", content="hi")], operation_id="op-add")
        assert captured[0]["body"]["operation_id"] == "op-add"
        assert result.operation_id == "op-add"
        assert result.add_record_id == "ar-1"
        assert result.memory_ids == ["m1"]
        assert result.status == "completed"
        assert result.replayed is False
        assert result.memories[0].memory_id == "m1"

    def test_add_omits_operation_id_when_not_given(self) -> None:
        captured: list[dict[str, Any]] = []
        _sync_client(captured).add([DialogueMessage(role="user", content="hi")])
        assert "operation_id" not in captured[0]["body"]

    def test_feedback_sends_window_and_operation_id(self) -> None:
        captured: list[dict[str, Any]] = []
        _sync_client(captured).feedback(
            operation_id="op-fb",
            window=FeedbackWindow(
                since_activity_id="ar-9",
                window_start="2026-01-01T00:00:00Z",
                window_end="2026-01-02T00:00:00Z",
            ),
        )
        body = captured[0]["body"]
        assert body["operation_id"] == "op-fb"
        assert body["window"] == {
            "since_activity_id": "ar-9",
            "window_start": "2026-01-01T00:00:00Z",
            "window_end": "2026-01-02T00:00:00Z",
        }

    def test_feedback_window_accepts_plain_dict(self) -> None:
        captured: list[dict[str, Any]] = []
        _sync_client(captured).feedback(window={"since_activity_id": "ar-9", "window_end": None})
        assert captured[0]["body"]["window"] == {"since_activity_id": "ar-9"}

    def test_dreaming_sends_seed_ids_and_operation_id(self) -> None:
        captured: list[dict[str, Any]] = []
        _sync_client(captured).dreaming(operation_id="op-dr", seed_add_record_ids=["ar-1", "ar-2"])
        body = captured[0]["body"]
        assert body["operation_id"] == "op-dr"
        assert body["seed_add_record_ids"] == ["ar-1", "ar-2"]

    def test_dreaming_empty_seed_list_is_sent_verbatim(self) -> None:
        captured: list[dict[str, Any]] = []
        _sync_client(captured).dreaming(seed_add_record_ids=[])
        assert captured[0]["body"]["seed_add_record_ids"] == []

    def test_dreaming_omits_seeds_when_not_given(self) -> None:
        captured: list[dict[str, Any]] = []
        _sync_client(captured).dreaming()
        assert "seed_add_record_ids" not in captured[0]["body"]

    def test_update_sends_fields_and_operation_id(self) -> None:
        captured: list[dict[str, Any]] = []
        _sync_client(captured).update("m1", fields={"user_id": "u2"}, operation_id="op-up")
        body = captured[0]["body"]
        assert body["memory_id"] == "m1"
        assert body["fields"] == {"user_id": "u2"}
        assert body["operation_id"] == "op-up"
        assert "content" not in body

    def test_update_content_still_supported(self) -> None:
        captured: list[dict[str, Any]] = []
        _sync_client(captured).update("m1", "new text")
        assert captured[0]["body"]["content"] == "new text"

    def test_delete_sends_operation_id(self) -> None:
        captured: list[dict[str, Any]] = []
        _sync_client(captured).delete("m1", operation_id="op-del")
        assert captured[0]["body"] == {"memory_id": "m1", "operation_id": "op-del"}

    def test_operation_status_issues_get_and_parses(self) -> None:
        captured: list[dict[str, Any]] = []
        status = _sync_client(captured).operation_status("op-1")
        assert captured[0]["method"] == "GET"
        assert captured[0]["path"] == "/v1/operations/op-1"
        assert isinstance(status, OperationStatus)
        assert status.operation_id == "op-1"
        assert status.kind == "add"
        assert status.status == "completed"
        assert status.result == {"memory_ids": ["m1"]}


@pytest.mark.asyncio
class TestAsyncLifecycleParity:
    async def test_add_sends_operation_id_and_parses_result(self) -> None:
        captured: list[dict[str, Any]] = []
        result = await _async_client(captured).add([DialogueMessage(role="user", content="hi")], operation_id="op-add")
        assert captured[0]["body"]["operation_id"] == "op-add"
        assert result.operation_id == "op-add"
        assert result.add_record_id == "ar-1"

    async def test_feedback_window_and_operation_id(self) -> None:
        captured: list[dict[str, Any]] = []
        await _async_client(captured).feedback(
            operation_id="op-fb",
            window={"window_start": "2026-01-01T00:00:00Z"},
        )
        body = captured[0]["body"]
        assert body["operation_id"] == "op-fb"
        assert body["window"] == {"window_start": "2026-01-01T00:00:00Z"}

    async def test_dreaming_seeds_and_operation_id(self) -> None:
        captured: list[dict[str, Any]] = []
        await _async_client(captured).dreaming(operation_id="op-dr", seed_add_record_ids=["ar-1"])
        body = captured[0]["body"]
        assert body["operation_id"] == "op-dr"
        assert body["seed_add_record_ids"] == ["ar-1"]

    async def test_update_fields_and_delete_operation_id(self) -> None:
        captured: list[dict[str, Any]] = []
        client = _async_client(captured)
        await client.update("m1", fields={"memory_type": "profile"}, operation_id="op-up")
        await client.delete("m1", operation_id="op-del")
        assert captured[0]["body"]["fields"] == {"memory_type": "profile"}
        assert captured[1]["body"]["operation_id"] == "op-del"

    async def test_operation_status_issues_get(self) -> None:
        captured: list[dict[str, Any]] = []
        status = await _async_client(captured).operation_status("op-9")
        assert captured[0]["method"] == "GET"
        assert captured[0]["path"] == "/v1/operations/op-9"
        assert status.status == "completed"
