"""Run submission and attachment upload routes:
- ``POST /api/sessions/{id}/attachments``
- ``POST /api/sessions/{id}/messages``
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI

from homemaster.application import RunPolicy, RunRequest
from homemaster.web._shared import (
    _WEB_PERMISSION_SUBJECT,
    _error,
    _provider_selection_error,
    _sanitize_filename,
    _session_attachment_dir,
    _session_ids,
    _web_attachments_root,
)
from homemaster.web.context import WebAppContext
from homemaster.web.event_hub import WebEventHub
from homemaster.web.run_registry import SessionBusyError, WebRunRegistry
from homemaster.web.schemas import (
    SendMessageAttachment,
    SendMessageRequest,
    UploadAttachmentRequest,
    WebEvent,
)

_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MiB
_INLINE_TEXT_MAX_BYTES = 64 * 1024  # 64 KiB
_ATTACHMENT_ID_RE = re.compile(r"^[a-f0-9]{16,32}\Z")
_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".csv",
    ".tsv",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".css",
    ".html",
    ".xml",
    ".sh",
    ".log",
    ".ini",
    ".cfg",
    ".sql",
}


def _resolve_session_attachment(
    root: Path,
    session_id: str,
    item: SendMessageAttachment,
) -> Path | None:
    if _ATTACHMENT_ID_RE.fullmatch(item.attachment_id) is None:
        return None
    session_dir = _session_attachment_dir(root, session_id)
    matches = list(session_dir.glob(f"{item.attachment_id}_*"))
    if not matches:
        return None
    resolved = matches[0].resolve()
    try:
        resolved.relative_to(session_dir)
    except ValueError:
        return None
    return resolved if resolved.is_file() else None


def _compose_message_with_attachments(
    text: str,
    resolved_files: list[tuple[SendMessageAttachment, Path]],
) -> str:
    base_text = text.strip()
    if not resolved_files:
        return base_text

    sections: list[str] = []
    if base_text:
        sections.append(base_text)
    else:
        names = ", ".join(item.filename for item, _ in resolved_files)
        sections.append(f"请查看并处理我上传的附件：{names}")

    sections.append("\n---\n[用户随消息上传的附件]")
    for item, path in resolved_files:
        ext = path.suffix.lower()
        is_text_mime = (
            item.media_type.startswith("text/")
            or item.media_type in {"application/json", "application/xml", "application/yaml"}
            or ext in _TEXT_EXTENSIONS
        )
        try:
            size = path.stat().st_size
        except OSError:
            size = -1
        header = f"- 文件名: `{item.filename}` (类型: `{item.media_type}`, 大小: {size} bytes, 本地路径: `{path}`)"
        if is_text_mime and 0 <= size <= _INLINE_TEXT_MAX_BYTES:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                sections.append(f"{header}\n  文件内容:\n```\n{content}\n```")
                continue
            except OSError:
                pass
        sections.append(
            f"{header}\n  （可通过文件读取工具或图像分析工具直接访问该本地路径 `{path}`）"
        )
    return "\n".join(sections)


def register(app: FastAPI, ctx: WebAppContext) -> None:
    application = ctx.application
    run_registry = ctx.run_registry
    pending_questions = ctx.pending_questions
    hub = ctx.hub
    config = ctx.config

    @app.post("/api/sessions/{session_id}/attachments", status_code=201)
    async def upload_attachment(session_id: str, body: UploadAttachmentRequest) -> object:
        if session_id not in _session_ids(application.session_manager):
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        raw_b64 = body.data_base64.strip()
        if "," in raw_b64 and raw_b64.startswith("data:"):
            raw_b64 = raw_b64.split(",", 1)[1]
        try:
            raw_bytes = base64.b64decode(raw_b64, validate=True)
        except (binascii.Error, ValueError):
            return _error(
                422,
                "invalid_attachment_data",
                "Attachment data_base64 is not valid base64.",
                retryable=False,
            )
        if len(raw_bytes) == 0:
            return _error(
                422,
                "empty_attachment",
                "Attachment file must not be empty.",
                retryable=False,
            )
        if len(raw_bytes) > _MAX_ATTACHMENT_BYTES:
            return _error(
                413,
                "attachment_too_large",
                "Attachment exceeds the 10 MiB limit.",
                retryable=False,
            )
        root = _web_attachments_root(application)
        session_dir = _session_attachment_dir(root, session_id)
        attachment_id = uuid4().hex[:24]
        safe_name = _sanitize_filename(body.filename)
        dest = (session_dir / f"{attachment_id}_{safe_name}").resolve()
        dest.relative_to(session_dir)

        def _write() -> None:
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                # os.write may return a short count; loop until the payload lands.
                view = memoryview(raw_bytes)
                while len(view) > 0:
                    view = view[os.write(fd, view) :]
            finally:
                os.close(fd)

        await asyncio.to_thread(_write)
        return {
            "attachment_id": attachment_id,
            "filename": safe_name,
            "media_type": body.media_type or "application/octet-stream",
            "size": len(raw_bytes),
        }

    @app.post("/api/sessions/{session_id}/messages", status_code=202)
    async def send_message(session_id: str, body: SendMessageRequest) -> object:
        if not body.text.strip() and not body.attachments:
            return _error(
                422,
                "invalid_request",
                "Either message text or at least one attachment is required.",
                retryable=False,
            )
        if session_id not in _session_ids(application.session_manager):
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        if await run_registry.is_accepted(session_id, body.request_id):
            return {
                "accepted": True,
                "session_id": session_id,
                "request_id": body.request_id,
            }
        selection_error = _provider_selection_error(
            config,
            provider_name=body.provider_name,
            model=body.model,
        )
        if selection_error is not None:
            return selection_error

        resolved_attachments: list[tuple[SendMessageAttachment, Path]] = []
        if body.attachments:
            root = _web_attachments_root(application)
            for item in body.attachments:
                resolved = _resolve_session_attachment(root, session_id, item)
                if resolved is None:
                    return _error(
                        422,
                        "attachment_not_found",
                        f"Attachment {item.filename!r} is missing or does not belong to this session.",
                        retryable=False,
                    )
                resolved_attachments.append((item, resolved))

        if not await hub.has_subscriber(session_id):
            return _error(
                409,
                "event_stream_not_ready",
                "Subscribe to the session event stream before sending a message.",
                retryable=True,
            )

        effective_text = _compose_message_with_attachments(body.text, resolved_attachments)

        async def ask_user_prompt(question: str) -> str:
            """Suspend ``ask_user_question`` until any client answers it."""

            run_id = await run_registry.active_run_id(session_id)
            if run_id is None:
                for _ in range(20):
                    await asyncio.sleep(0.05)
                    run_id = await run_registry.active_run_id(session_id)
                    if run_id is not None:
                        break
            try:
                from homemaster.substrate.toolkit import current_tool_call_id

                tool_call_id = current_tool_call_id()
            except Exception:
                tool_call_id = ""
            record = await pending_questions.ask(
                session_id=session_id,
                run_id=run_id or "",
                request_id=body.request_id,
                question=question,
                tool_call_id=tool_call_id,
            )
            await hub.publish(
                WebEvent(
                    type="question.asked",
                    session_id=session_id,
                    run_id=record.run_id,
                    request_id=record.request_id,
                    payload={
                        "question_id": record.question_id,
                        "question": record.question,
                        "tool_call_id": record.tool_call_id,
                    },
                )
            )
            return await record.wait()

        start_gate = asyncio.Event()

        def _schedule_run_finished(done_task: asyncio.Task[object]) -> None:
            emitter = hub.note_task_finished(session_id, body.request_id, done_task)
            try:
                asyncio.get_running_loop().create_task(emitter)
            except RuntimeError:
                emitter.close()

        async def run_owned() -> object:
            owned_task = asyncio.current_task()
            if owned_task is not None:
                owned_task.add_done_callback(_schedule_run_finished)
            await start_gate.wait()
            try:
                return await _run_and_report_prestart_failure(
                    application=application,
                    request=RunRequest(
                        text=effective_text,
                        session_id=session_id,
                        resume=True,
                        provider_name=body.provider_name,
                        model_override=body.model,
                        permission_subject=_WEB_PERMISSION_SUBJECT,
                        run_policy=RunPolicy(
                            max_tool_iterations=(
                                config.runtime.max_tool_iterations if config is not None else None
                            ),
                        ),
                        metadata={"web_request_id": body.request_id},
                        dependencies={"ask_user_prompt": ask_user_prompt},
                    ),
                    request_id=body.request_id,
                    run_registry=run_registry,
                    hub=hub,
                )
            finally:
                for record in await pending_questions.cancel_request(session_id, body.request_id):
                    await hub.publish(
                        WebEvent(
                            type="question.cancelled",
                            session_id=session_id,
                            run_id=record.run_id,
                            request_id=record.request_id,
                            payload={"question_id": record.question_id},
                        )
                    )

        try:
            acceptance = await run_registry.accept(
                session_id,
                body.request_id,
                run_owned,
            )
        except SessionBusyError:
            return _error(
                409,
                "session_busy",
                "This session already has an active run.",
                retryable=True,
            )
        if acceptance.created:
            await hub.publish(
                WebEvent(
                    type="request.accepted",
                    session_id=session_id,
                    run_id="",
                    request_id=body.request_id,
                    payload={},
                )
            )
            start_gate.set()
        return {
            "accepted": True,
            "session_id": session_id,
            "request_id": body.request_id,
        }


async def _run_and_report_prestart_failure(
    *,
    application: Any,
    request: RunRequest,
    request_id: str,
    run_registry: WebRunRegistry,
    hub: WebEventHub,
) -> object | None:
    """Turn adapter failures before ``turn_started`` into one safe terminal event."""

    try:
        return await application.run(request)
    except Exception:
        session_id = request.session_id or ""
        if await run_registry.fail_before_start(session_id, request_id):
            await hub.publish(
                WebEvent(
                    type="run.failed",
                    session_id=session_id,
                    run_id="",
                    request_id=request_id,
                    payload={
                        "code": "adapter_failed",
                        "message": "The run could not be started.",
                        "retryable": True,
                    },
                )
            )
        return None


__all__ = ["register"]
