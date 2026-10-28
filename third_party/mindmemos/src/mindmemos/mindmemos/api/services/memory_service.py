"""Memory HTTP business logic."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal
from uuid import uuid4

from ...config import get_config
from ...errors import ConflictError, MemoryNotFoundError, OperationProcessingError, ResourceNotFoundError
from ...logging import get_logger, traced
from ...pipelines import create_pipeline
from ...pipelines.add import AddPipeline
from ...pipelines.delete import DefaultDeletePipeline, DeletePipeline
from ...pipelines.dreaming import DreamingPipeline
from ...pipelines.feedback import FeedbackPipeline
from ...pipelines.get import DefaultGetPipeline, GetPipeline
from ...pipelines.memory_db import MemoryCatalog, MemoryOperationRecorder, suppress_recording_errors, utcnow
from ...pipelines.search import SearchPipeline
from ...pipelines.skill import SkillVersionStore, get_skill_version_store
from ...pipelines.update import DefaultUpdatePipeline, UpdatePipeline
from ...provider_bindings import ProviderBindingResolver, provider_config_context
from ...services import (
    OperationLedger,
    add_operation_result,
    canonical_request_hash,
    dreaming_operation_result,
    feedback_operation_result,
    get_operation_ledger,
)
from ...typing import (
    AddPipelineAsyncResult,
    AddPipelineSyncResult,
    AddStreamCancelled,
    DeletePipelineResult,
    DreamingPipelineResult,
    FeedbackPipelineResult,
    GetPipelineResult,
    MemoryAddEventItem,
    MemoryListPipelineResult,
    MemoryScrollPipelineResult,
    SearchPipelineResult,
    SkillBinding,
    SkillContext,
    UpdatePipelineResult,
)
from ..algorithm import binding_for_memory_algorithm
from ..deps import annotate_request_trace
from ..mappers import (
    to_add_pipeline_input,
    to_delete_pipeline_input,
    to_dreaming_pipeline_input,
    to_feedback_pipeline_input,
    to_get_pipeline_input,
    to_memory_list_pipeline_input,
    to_memory_request_context,
    to_memory_scroll_pipeline_input,
    to_search_pipeline_input,
    to_update_pipeline_input,
)
from ..schemas import (
    AddRequest,
    AuthContext,
    DeleteRequest,
    DreamingRequest,
    FeedbackRequest,
    GetRequest,
    MemoryPageRequest,
    MemoryScrollRequest,
    SearchRequest,
    UpdateRequest,
)

logger = get_logger(__name__)

PipelineKind = Literal["add", "search", "get", "delete", "update", "feedback", "dreaming"]
SEARCH_PIPELINE_NAME = "search_pipeline"


class MemoryService:
    """Stateless facade routing memory endpoints to their pipelines."""

    def __init__(
        self,
        *,
        get_pipeline: GetPipeline | None = None,
        catalog: MemoryCatalog | None = None,
        add_pipeline: AddPipeline | None = None,
        search_pipeline: SearchPipeline | None = None,
        delete_pipeline: DeletePipeline | None = None,
        update_pipeline: UpdatePipeline | None = None,
        feedback_pipeline: FeedbackPipeline | None = None,
        dreaming_pipeline: DreamingPipeline | None = None,
        add_pipeline_name: str | None = None,
        search_pipeline_name: str | None = None,
        get_pipeline_name: str | None = None,
        delete_pipeline_name: str | None = None,
        update_pipeline_name: str | None = None,
        feedback_pipeline_name: str | None = None,
        dreaming_pipeline_name: str | None = None,
        operation_recorder: MemoryOperationRecorder | None = None,
        skill_store: SkillVersionStore | None = None,
        provider_binding_resolver: ProviderBindingResolver | None = None,
        operation_ledger: OperationLedger | None = None,
    ) -> None:
        self._add = add_pipeline
        if search_pipeline is None and search_pipeline_name is None:
            search_pipeline_name = SEARCH_PIPELINE_NAME
        self._search = search_pipeline
        self._get = get_pipeline if get_pipeline is not None else (None if get_pipeline_name else DefaultGetPipeline())
        self._catalog = catalog or MemoryCatalog()
        self._delete = (
            delete_pipeline
            if delete_pipeline is not None
            else (None if delete_pipeline_name else DefaultDeletePipeline())
        )
        self._update = (
            update_pipeline
            if update_pipeline is not None
            else (None if update_pipeline_name else DefaultUpdatePipeline())
        )
        self._feedback = feedback_pipeline
        self._dreaming = dreaming_pipeline
        self._pipeline_names: dict[str, tuple[PipelineKind, str | None]] = {
            "_add": ("add", add_pipeline_name),
            "_search": ("search", search_pipeline_name),
            "_get": ("get", get_pipeline_name),
            "_delete": ("delete", delete_pipeline_name),
            "_update": ("update", update_pipeline_name),
            "_feedback": ("feedback", feedback_pipeline_name),
            "_dreaming": ("dreaming", dreaming_pipeline_name),
        }
        self._recorder = operation_recorder or MemoryOperationRecorder()
        self._skill_store = skill_store if skill_store is not None else get_skill_version_store()
        self._algorithm_add_pipelines: dict[str, AddPipeline] = {}
        self._provider_binding_resolver = provider_binding_resolver
        self._operation_ledger = operation_ledger

    def _operations(self) -> OperationLedger:
        """Return the durable operation ledger (V3.8)."""

        return self._operation_ledger or get_operation_ledger()

    def _pipeline(self, attr: str):
        pipeline = getattr(self, attr)
        if pipeline is not None:
            return pipeline
        pipeline_type, pipeline_name = self._pipeline_names[attr]
        if pipeline_name is None:
            return None
        pipeline = create_pipeline(type=pipeline_type, name=pipeline_name)
        setattr(self, attr, pipeline)
        return pipeline

    def _add_pipeline_for_algorithm(self, memory_algorithm: str) -> tuple[AddPipeline | None, str | None]:
        pipeline_name = binding_for_memory_algorithm(memory_algorithm).add_pipeline
        pipeline = self._algorithm_add_pipelines.get(pipeline_name)
        if pipeline is None:
            pipeline = create_pipeline(type="add", name=pipeline_name)
            self._algorithm_add_pipelines[pipeline_name] = pipeline
        return pipeline, pipeline_name

    def _add_pipeline_for_auth(self, auth: AuthContext) -> tuple[AddPipeline | None, str | None]:
        if self._add is not None:
            return self._add, self._pipeline_names["_add"][1]
        return self._add_pipeline_for_algorithm(auth.memory_algorithm)

    async def _provider_config_context(self, ctx):
        """Return a temporary config binding that includes dynamic provider overrides."""
        return await provider_config_context(
            ctx,
            resolver=self._provider_binding_resolver,
        )

    @traced("memory_service.add")
    async def add(
        self,
        auth: AuthContext,
        request: AddRequest,
    ) -> AddPipelineSyncResult | AddPipelineAsyncResult:
        """Run the add pipeline according to the requested mode.

        V3.8: every call is ledgered under ``operation_id`` (client-supplied or
        ``add:<uuid>``). The ledger reservation is taken before the add-record
        reservation and any side effect; retries replay or reuse the stored
        reservation per spec §1/§2.
        """

        # Stamp request identity onto the handler-root span so downstream LLM spans
        # (which live in this trace, not the auth dependency's trace) are attributable.
        annotate_request_trace(auth)
        pipeline, _add_pipeline_name = self._add_pipeline_for_auth(auth)
        if pipeline is None:
            raise NotImplementedError("add pipeline implementation is not wired yet")
        ctx = to_memory_request_context(auth, request, require_user_id=True)
        payload = to_add_pipeline_input(request)

        operation_id = request.operation_id or f"add:{uuid4()}"
        payload.operation_id = operation_id
        request_hash = canonical_request_hash(request.model_dump(mode="json", exclude={"operation_id"}))
        ledger = self._operations()
        candidate_record_id = str(uuid4())
        outcome = await ledger.reserve(
            ctx.project_id,
            operation_id,
            kind="add",
            request_hash=request_hash,
            ref_id=candidate_record_id,
        )
        if outcome.decision == "conflict":
            raise ConflictError(f"operation_id {operation_id!r} was already used with a different payload")
        if outcome.decision == "processing":
            raise OperationProcessingError(f"operation_id {operation_id!r} is still processing")
        if outcome.decision == "replay":
            stored = dict(outcome.result or {})
            data = add_operation_result(
                operation_id,
                str(stored.get("add_record_id") or outcome.record.ref_id or ""),
                memory_ids=[str(v) for v in stored.get("memory_ids") or []],
                memories=[v for v in stored.get("memories") or [] if isinstance(v, dict)],
                status=str(stored.get("status") or "completed"),
                replayed=True,
            )
            result = AddPipelineSyncResult(
                status="ok",
                memories=[MemoryAddEventItem.model_validate(m) for m in data["memories"]],
                memory_ids=list(data["memory_ids"]),
                data_status=str(stored.get("status") or "completed"),
            )
            result.operation_data = data
            return result

        add_record_id = outcome.record.ref_id or candidate_record_id
        request_submitted_at = utcnow()
        config_ctx = await self._provider_config_context(ctx)
        try:
            with config_ctx:
                skill_bindings = await self._bind_skill_context(ctx.project_id, add_record_id, request.skill_context)
                result = await self._add_with_context(
                    pipeline,
                    payload,
                    ctx,
                    request,
                    add_record_id,
                    request_submitted_at,
                    skill_bindings,
                    extra_payload={"operation_id": operation_id, "request_hash": request_hash},
                )
        except Exception as exc:
            await suppress_recording_errors(
                ledger.fail(ctx.project_id, operation_id, error=str(exc)),
                operation="add",
            )
            raise

        data_status = _add_data_status(payload.mode, result)
        data = add_operation_result(
            operation_id,
            add_record_id,
            memory_ids=list(getattr(result, "memory_ids", None) or []),
            memories=[m.model_dump(mode="json") for m in getattr(result, "memories", []) or []],
            status=data_status,
        )
        await suppress_recording_errors(
            ledger.complete(
                ctx.project_id,
                operation_id,
                status=data_status,
                result=data,
                # 'accepted' hands the lease to the async worker — clearing it
                # here would let a resubmit re-reserve and double-execute.
                clear_lease=data_status != "accepted",
            ),
            operation="add",
        )
        result.operation_data = data
        return result

    @traced("memory_service.add_stream")
    async def add_stream(
        self,
        auth: AuthContext,
        request: AddRequest,
        *,
        cancel_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Run sync add while streaming progress events."""

        annotate_request_trace(auth)
        pipeline, _add_pipeline_name = self._add_pipeline_for_auth(auth)
        if pipeline is None:
            raise NotImplementedError("add pipeline implementation is not wired yet")
        ctx = to_memory_request_context(auth, request, require_user_id=True)
        payload = to_add_pipeline_input(request)
        if payload.mode != "sync":
            yield {
                "event": "error",
                "stage": "accepted",
                "message": "streaming add only supports sync mode",
            }
            return

        add_record_id = str(uuid4())
        request_submitted_at = utcnow()
        config_ctx = await self._provider_config_context(ctx)
        with config_ctx:
            skill_bindings = await self._bind_skill_context(ctx.project_id, add_record_id, request.skill_context)
            yield {
                "event": "progress",
                "stage": "accepted",
                "message": "Add request accepted.",
                "percent": 1,
            }
            await suppress_recording_errors(
                self._recorder.record_add_input(
                    payload,
                    ctx=ctx,
                    request_submitted_at=request_submitted_at,
                    add_record_id=add_record_id,
                    status="processing",
                    skill_bindings=skill_bindings,
                    score=request.score,
                    task_id=request.task_id,
                ),
                operation="add",
            )

            if hasattr(pipeline, "add_sync_stream"):
                async for event in self._stream_pipeline_add(
                    pipeline,
                    payload,
                    ctx,
                    add_record_id=add_record_id,
                    cancel_check=cancel_check,
                ):
                    yield event
                return

            try:
                result = await pipeline.add_sync(payload, ctx, add_record_id=add_record_id)
            except Exception as exc:
                await suppress_recording_errors(
                    self._recorder.mark_add_failed(ctx, add_record_id, str(exc)),
                    operation="add",
                )
                yield {"event": "error", "stage": "error", "message": str(exc)}
                return
            yield {
                "event": "completed",
                "stage": "completed",
                "message": "Add completed.",
                "data": {"memories": [memory.model_dump(mode="json") for memory in result.memories]},
            }

    async def _stream_pipeline_add(
        self,
        pipeline: AddPipeline,
        payload,
        ctx,
        *,
        add_record_id: str,
        cancel_check: Callable[[], Awaitable[bool]] | None,
    ) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def progress(
            stage: str,
            message: str,
            percent: int | None = None,
            data: dict[str, Any] | None = None,
        ) -> None:
            event: dict[str, Any] = {
                "event": "progress",
                "stage": stage,
                "message": message,
            }
            if percent is not None:
                event["percent"] = percent
            if data:
                message_i18n = data.get("message_i18n")
                if isinstance(message_i18n, dict):
                    event["message_i18n"] = message_i18n
                remaining_data = {key: value for key, value in data.items() if key != "message_i18n"}
                if remaining_data:
                    event["data"] = remaining_data
            await queue.put(event)

        async def run_pipeline() -> None:
            try:
                result_or_stream = pipeline.add_sync_stream(
                    payload,
                    ctx,
                    add_record_id=add_record_id,
                    progress=progress,
                    cancel_check=cancel_check,
                )
                if hasattr(result_or_stream, "__aiter__"):
                    async for event in result_or_stream:
                        await queue.put(event)
                else:
                    result = await result_or_stream
                    await queue.put(
                        {
                            "event": "completed",
                            "stage": "completed",
                            "message": "Add completed.",
                            "data": {"memories": [memory.model_dump(mode="json") for memory in result.memories]},
                        }
                    )
            except AddStreamCancelled as exc:
                await suppress_recording_errors(
                    self._recorder.mark_add_cancelled(ctx, add_record_id, exc.message),
                    operation="add",
                )
                await queue.put(
                    {
                        "event": "cancelled",
                        "stage": exc.stage,
                        "message": exc.message,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                await suppress_recording_errors(
                    self._recorder.mark_add_failed(ctx, add_record_id, str(exc)),
                    operation="add",
                )
                await queue.put({"event": "error", "stage": "error", "message": str(exc)})
            finally:
                await queue.put(None)

        task = asyncio.create_task(run_pipeline())
        heartbeat_seconds = getattr(self, "stream_heartbeat_seconds", 10.0)
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
                except asyncio.TimeoutError:
                    yield {
                        "event": "heartbeat",
                        "stage": "waiting",
                        "message": "Memory extraction is still running.",
                        "message_i18n": {
                            "zh": "记忆提取仍在进行，请稍候",
                            "en": "Memory extraction is still running.",
                        },
                    }
                    continue
                if item is None:
                    break
                yield item
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _add_with_context(
        self,
        pipeline: AddPipeline,
        payload,
        ctx,
        request: AddRequest,
        add_record_id: str,
        request_submitted_at,
        skill_bindings,
        *,
        extra_payload: dict | None = None,
    ):
        try:
            if payload.mode == "async":
                record_metadata = {
                    "request_submitted_at": request_submitted_at.isoformat(),
                    "skill_bindings": [binding.model_dump(mode="json") for binding in skill_bindings or []],
                    "score": request.score,
                    "task_id": request.task_id,
                }
                await suppress_recording_errors(
                    self._recorder.record_add_input(
                        payload,
                        ctx=ctx,
                        request_submitted_at=request_submitted_at,
                        add_record_id=add_record_id,
                        status="queued",
                        skill_bindings=skill_bindings,
                        score=request.score,
                        task_id=request.task_id,
                        extra_payload=extra_payload,
                    ),
                    operation="add",
                )
                return await pipeline.add_async(
                    payload,
                    ctx,
                    add_record_id=add_record_id,
                    record_metadata=record_metadata,
                )
            await suppress_recording_errors(
                self._recorder.record_add_input(
                    payload,
                    ctx=ctx,
                    request_submitted_at=request_submitted_at,
                    add_record_id=add_record_id,
                    status="processing",
                    skill_bindings=skill_bindings,
                    score=request.score,
                    task_id=request.task_id,
                    extra_payload=extra_payload,
                ),
                operation="add",
            )
            return await pipeline.add_sync(payload, ctx, add_record_id=add_record_id)
        except Exception as exc:
            await suppress_recording_errors(
                self._recorder.mark_add_failed(ctx, add_record_id, str(exc)),
                operation="add",
            )
            raise

    async def _bind_skill_context(
        self,
        project_id: str,
        add_record_id: str,
        skill_context: list[SkillContext] | None,
    ) -> list[SkillBinding] | None:
        """Resolve ``skill_context`` to trace bindings, swallowing all failures."""

        if self._skill_store is None or not skill_context:
            return None
        try:
            return await self._skill_store.bind_skill_context(
                project_id=project_id,
                add_record_id=add_record_id,
                skill_context=skill_context,
            )
        except Exception as exc:  # noqa: BLE001 - binding must never block add
            logger.warning("skill_context binding failed", error=str(exc), add_record_id=add_record_id)
            return None

    @traced("memory_service.search")
    async def search(
        self,
        auth: AuthContext,
        request: SearchRequest,
    ) -> SearchPipelineResult:
        """Run the search pipeline."""

        # Stamp request identity onto the handler-root span so downstream LLM spans
        # (which live in this trace, not the auth dependency's trace) are attributable.
        annotate_request_trace(auth)
        ctx = to_memory_request_context(auth, request)
        binding = binding_for_memory_algorithm(auth.memory_algorithm)
        payload = to_search_pipeline_input(request, search_pipeline=binding.search_pipeline)
        pipeline = self._pipeline("_search")
        if pipeline is None:
            raise NotImplementedError("search pipeline implementation is not wired yet")
        config_ctx = await self._provider_config_context(ctx)
        with config_ctx:
            return await self._search_with_context(pipeline, payload, ctx)

    async def _search_with_context(self, pipeline: SearchPipeline, payload, ctx) -> SearchPipelineResult:
        request_submitted_at = utcnow()
        try:
            result = await pipeline.search(payload, ctx)
        except Exception:
            task_completed_at = utcnow()
            await suppress_recording_errors(
                self._recorder.record_search(
                    payload,
                    None,
                    ctx=ctx,
                    request_submitted_at=request_submitted_at,
                    task_completed_at=task_completed_at,
                ),
                operation="search",
            )
            raise
        task_completed_at = utcnow()
        search_record_id = await suppress_recording_errors(
            self._recorder.record_search(
                payload,
                result,
                ctx=ctx,
                request_submitted_at=request_submitted_at,
                task_completed_at=task_completed_at,
            ),
            operation="search",
        )
        result.search_record_id = search_record_id
        return result

    @traced("memory_service.get")
    async def get(self, auth: AuthContext, request: GetRequest) -> GetPipelineResult:
        """Run the get pipeline."""

        # Stamp request identity onto the handler-root span so downstream LLM spans
        # (which live in this trace, not the auth dependency's trace) are attributable.
        annotate_request_trace(auth)
        pipeline = self._pipeline("_get")
        if pipeline is None:
            raise NotImplementedError("get pipeline implementation is not wired yet")
        ctx = to_memory_request_context(auth)
        config_ctx = await self._provider_config_context(ctx)
        with config_ctx:
            return await pipeline.get(to_get_pipeline_input(request), ctx)

    @traced("memory_service.list")
    async def list(self, auth: AuthContext, request: MemoryPageRequest) -> MemoryListPipelineResult:
        """Run the paged memory list pipeline."""

        annotate_request_trace(auth)
        ctx = to_memory_request_context(auth, request)
        config_ctx = await self._provider_config_context(ctx)
        with config_ctx:
            return await self._catalog.list(to_memory_list_pipeline_input(request), ctx)

    @traced("memory_service.scroll")
    async def scroll(self, auth: AuthContext, request: MemoryScrollRequest) -> MemoryScrollPipelineResult:
        """Run the cursor memory scroll pipeline."""

        annotate_request_trace(auth)
        ctx = to_memory_request_context(auth, request)
        config_ctx = await self._provider_config_context(ctx)
        with config_ctx:
            return await self._catalog.scroll(to_memory_scroll_pipeline_input(request), ctx)

    @traced("memory_service.delete")
    async def delete(self, auth: AuthContext, request: DeleteRequest) -> DeletePipelineResult:
        """Run the delete pipeline."""

        # Stamp request identity onto the handler-root span so downstream LLM spans
        # (which live in this trace, not the auth dependency's trace) are attributable.
        annotate_request_trace(auth)
        pipeline = self._pipeline("_delete")
        if pipeline is None:
            raise NotImplementedError("delete pipeline implementation is not wired yet")
        ctx = to_memory_request_context(auth)
        operation_id = request.operation_id
        ledger = self._operations() if operation_id else None
        outcome = None
        if operation_id:
            outcome = await ledger.reserve(
                ctx.project_id,
                operation_id,
                kind="delete",
                request_hash=canonical_request_hash(request.model_dump(mode="json", exclude={"operation_id"})),
                ref_id=request.id,
            )
            self._raise_for_decision(outcome.decision, operation_id)
            if outcome.decision == "replay":
                replay_result = DeletePipelineResult(status="ok")
                replay_result.operation_data = _replayed_data(outcome, operation_id)
                return replay_result
        try:
            result = await pipeline.delete(to_delete_pipeline_input(request), ctx)
        except MemoryNotFoundError as exc:
            if operation_id:
                await suppress_recording_errors(
                    ledger.fail(ctx.project_id, operation_id, error=str(exc)),
                    operation="delete",
                )
            raise ResourceNotFoundError(str(exc), code="memory.not_found") from exc
        except Exception as exc:
            if operation_id:
                await suppress_recording_errors(
                    ledger.fail(ctx.project_id, operation_id, error=str(exc)),
                    operation="delete",
                )
            raise
        if operation_id:
            data = {
                "operation_id": operation_id,
                "memory_id": request.id,
                "status": "completed",
                "replayed": False,
            }
            await suppress_recording_errors(
                ledger.complete(ctx.project_id, operation_id, status="completed", result=data),
                operation="delete",
            )
            result.operation_data = data
        return result

    @traced("memory_service.update")
    async def update(self, auth: AuthContext, request: UpdateRequest) -> UpdatePipelineResult:
        """Run the update pipeline."""

        # Stamp request identity onto the handler-root span so downstream LLM spans
        # (which live in this trace, not the auth dependency's trace) are attributable.
        annotate_request_trace(auth)
        pipeline = self._pipeline("_update")
        if pipeline is None:
            raise NotImplementedError("update pipeline implementation is not wired yet")
        ctx = to_memory_request_context(auth, request)
        operation_id = request.operation_id
        ledger = self._operations() if operation_id else None
        outcome = None
        if operation_id:
            outcome = await ledger.reserve(
                ctx.project_id,
                operation_id,
                kind="update",
                request_hash=canonical_request_hash(request.model_dump(mode="json", exclude={"operation_id"})),
                ref_id=request.id,
            )
            self._raise_for_decision(outcome.decision, operation_id)
            if outcome.decision == "replay":
                replay_result = UpdatePipelineResult(status="ok")
                replay_result.operation_data = _replayed_data(outcome, operation_id)
                return replay_result
        config_ctx = await self._provider_config_context(ctx)
        try:
            with config_ctx:
                result = await pipeline.update(to_update_pipeline_input(request), ctx)
        except MemoryNotFoundError as exc:
            if operation_id:
                await suppress_recording_errors(
                    ledger.fail(ctx.project_id, operation_id, error=str(exc)),
                    operation="update",
                )
            raise ResourceNotFoundError(str(exc), code="memory.not_found") from exc
        except Exception as exc:
            if operation_id:
                await suppress_recording_errors(
                    ledger.fail(ctx.project_id, operation_id, error=str(exc)),
                    operation="update",
                )
            raise
        if operation_id:
            data = {
                "operation_id": operation_id,
                "memory_id": request.id,
                "status": "completed",
                "replayed": False,
            }
            await suppress_recording_errors(
                ledger.complete(ctx.project_id, operation_id, status="completed", result=data),
                operation="update",
            )
            result.operation_data = data
        return result

    @traced("memory_service.feedback")
    async def feedback(self, auth: AuthContext, request: FeedbackRequest) -> FeedbackPipelineResult:
        """Run the feedback pipeline under the V3.8 operation ledger.

        ``operation_id`` is client-supplied or auto-minted; the ledger reserves
        before any side effect and stores ``feedback_record_id`` + executed
        actions for replay/reconciliation. ``window`` narrows implicit
        candidate collection and flows through the pipeline input unchanged.
        """

        # Stamp request identity onto the handler-root span so downstream LLM spans
        # (which live in this trace, not the auth dependency's trace) are attributable.
        annotate_request_trace(auth)
        pipeline = self._pipeline("_feedback")
        if pipeline is None:
            raise NotImplementedError("feedback pipeline implementation is not wired yet")
        ctx = to_memory_request_context(auth, request, require_user_id=True)
        payload = to_feedback_pipeline_input(request)

        operation_id = request.operation_id or f"feedback:{uuid4()}"
        feedback_record_id = str(uuid4())
        payload.operation_id = operation_id
        payload.feedback_record_id = feedback_record_id
        request_hash = canonical_request_hash(request.model_dump(mode="json", exclude={"operation_id"}))
        ledger = self._operations()
        outcome = await ledger.reserve(
            ctx.project_id,
            operation_id,
            kind="feedback",
            request_hash=request_hash,
            ref_id=feedback_record_id,
        )
        self._raise_for_decision(outcome.decision, operation_id)
        if outcome.decision == "replay":
            stored = dict(outcome.result or {})
            replay_result = FeedbackPipelineResult(status="ok")
            replay_result.operation_data = feedback_operation_result(
                operation_id,
                str(stored.get("feedback_record_id") or feedback_record_id),
                actions=[v for v in stored.get("actions") or [] if isinstance(v, dict)],
                status=str(stored.get("status") or "completed"),
                replayed=True,
            )
            return replay_result

        config_ctx = await self._provider_config_context(ctx)
        try:
            with config_ctx:
                if payload.mode == "async":
                    result = await pipeline.feedback_async(payload, ctx)
                else:
                    result = await pipeline.feedback_sync(payload, ctx)
        except Exception as exc:
            await suppress_recording_errors(
                ledger.fail(ctx.project_id, operation_id, error=str(exc)),
                operation="feedback",
            )
            raise

        is_async = payload.mode == "async"
        actions = _feedback_action_dicts(result.actions, record_id=feedback_record_id)
        data_status = "accepted" if is_async else ("no_action" if not result.actions else "completed")
        data = feedback_operation_result(
            operation_id,
            feedback_record_id,
            actions=actions,
            status=data_status,
        )
        await suppress_recording_errors(
            ledger.complete(
                ctx.project_id,
                operation_id,
                status=data_status,
                result=data,
                clear_lease=data_status != "accepted",
            ),
            operation="feedback",
        )
        result.operation_data = data
        return result

    @traced("memory_service.dream")
    async def dream(self, auth: AuthContext, request: DreamingRequest) -> DreamingPipelineResult:
        """Run the dreaming pipeline under the V3.8 operation ledger.

        ``seed_add_record_ids`` scopes the run to exactly those add records;
        an empty list is a successful ``no_action``. The dreaming record id is
        minted at reservation and reused as the consolidation run id.
        """

        # Stamp request identity onto the handler-root span so downstream LLM spans
        # (which live in this trace, not the auth dependency's trace) are attributable.
        annotate_request_trace(auth)
        pipeline = self._pipeline("_dreaming")
        if pipeline is None:
            raise NotImplementedError("dreaming pipeline implementation is not wired yet")
        payload = to_dreaming_pipeline_input(request)
        ctx = to_memory_request_context(auth, request)

        operation_id = request.operation_id or f"dreaming:{uuid4()}"
        dreaming_record_id = str(uuid4())
        payload.operation_id = operation_id
        payload.dreaming_record_id = dreaming_record_id
        request_hash = canonical_request_hash(request.model_dump(mode="json", exclude={"operation_id"}))
        ledger = self._operations()
        outcome = await ledger.reserve(
            ctx.project_id,
            operation_id,
            kind="dreaming",
            request_hash=request_hash,
            ref_id=dreaming_record_id,
        )
        self._raise_for_decision(outcome.decision, operation_id)
        if outcome.decision == "replay":
            stored = dict(outcome.result or {})
            replay_result = DreamingPipelineResult(
                status="ok",
                dreaming_record_id=str(stored.get("dreaming_record_id") or dreaming_record_id),
            )
            replay_result.operation_data = dreaming_operation_result(
                operation_id,
                str(stored.get("dreaming_record_id") or dreaming_record_id),
                produced_memory_ids=[str(v) for v in stored.get("produced_memory_ids") or []],
                actions=[v for v in stored.get("actions") or [] if isinstance(v, dict)],
                status=str(stored.get("status") or "completed"),
                replayed=True,
            )
            return replay_result

        if payload.seed_add_record_ids is not None and not payload.seed_add_record_ids:
            # Explicit empty seed list → successful no-op (V3.8 §5).
            data = dreaming_operation_result(operation_id, dreaming_record_id, status="no_action")
            await suppress_recording_errors(
                ledger.complete(ctx.project_id, operation_id, status="no_action", result=data),
                operation="dreaming",
            )
            result = DreamingPipelineResult(
                status="ok",
                message="empty seed_add_record_ids: nothing to consolidate",
                dreaming_record_id=dreaming_record_id,
                data_status="no_action",
            )
            result.operation_data = data
            return result

        config_ctx = await self._provider_config_context(ctx)
        try:
            with config_ctx:
                if payload.mode == "sync":
                    result = await pipeline.dream_sync(payload, ctx)
                else:
                    result = await pipeline.dream(payload, ctx)
        except Exception as exc:
            await suppress_recording_errors(
                ledger.fail(ctx.project_id, operation_id, error=str(exc)),
                operation="dreaming",
            )
            raise

        is_async = payload.mode != "sync"
        data_status = "accepted" if is_async else (result.data_status or "completed")
        data = dreaming_operation_result(
            operation_id,
            result.dreaming_record_id or dreaming_record_id,
            produced_memory_ids=list(result.produced_memory_ids or []),
            actions=list(result.actions or []),
            status=data_status,
        )
        await suppress_recording_errors(
            ledger.complete(
                ctx.project_id,
                operation_id,
                status=data_status,
                result=data,
                clear_lease=data_status != "accepted",
            ),
            operation="dreaming",
        )
        result.operation_data = data
        return result

    @staticmethod
    def _raise_for_decision(decision: str, operation_id: str) -> None:
        """Translate a ledger reserve decision into an API error when needed."""

        if decision == "conflict":
            raise ConflictError(f"operation_id {operation_id!r} was already used with a different payload")
        if decision == "processing":
            raise OperationProcessingError(f"operation_id {operation_id!r} is still processing")


def _add_data_status(mode: str, result) -> str:
    """Ledger status for one add result (``accepted``/``completed``/``no_action``)."""

    if mode == "async":
        return "accepted"
    hinted = getattr(result, "data_status", None)
    if hinted:
        return hinted
    memory_ids = list(getattr(result, "memory_ids", None) or [])
    memories = list(getattr(result, "memories", None) or [])
    return "no_action" if not memories and not memory_ids else "completed"


def _feedback_action_dicts(actions, *, record_id: str) -> list[dict[str, Any]]:
    """Map feedback action results into ``{action_id, action, memory_id}`` records."""

    out: list[dict[str, Any]] = []
    for index, action in enumerate(actions or []):
        memory_id = getattr(action, "result_memory_id", None) or getattr(action, "target_memory_id", None)
        out.append(
            {
                "action_id": f"{record_id}:{index}",
                "action": getattr(action, "action", "noop"),
                "memory_id": memory_id,
            }
        )
    return out


def _replayed_data(outcome, operation_id: str) -> dict[str, Any]:
    """Replayed ``data`` payload for management operations (update/delete)."""

    stored = dict(outcome.result or {})
    data = {
        "operation_id": operation_id,
        "status": str(stored.get("status") or "completed"),
        "replayed": True,
    }
    for key in ("memory_id",):
        if key in stored:
            data[key] = stored[key]
    return data


_service: MemoryService | None = None
_service_key: tuple[str, ...] | None = None


def get_memory_service() -> MemoryService:
    """Process-global service singleton, used as a FastAPI dependency."""

    global _service, _service_key
    cfg = get_config().pipelines
    get_pipeline = cfg["get"]
    delete_pipeline = cfg["delete"]
    update_pipeline = cfg["update"]
    feedback_pipeline = cfg["feedback"]
    dreaming_pipeline = cfg["dreaming"]
    service_key = (
        get_pipeline,
        delete_pipeline,
        update_pipeline,
        feedback_pipeline,
        dreaming_pipeline,
    )
    if _service is None or _service_key != service_key:
        _service = MemoryService(
            get_pipeline_name=get_pipeline,
            delete_pipeline_name=delete_pipeline,
            update_pipeline_name=update_pipeline,
            feedback_pipeline_name=feedback_pipeline,
            dreaming_pipeline_name=dreaming_pipeline,
        )
        _service_key = service_key
    return _service
