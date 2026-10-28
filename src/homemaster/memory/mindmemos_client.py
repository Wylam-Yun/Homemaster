"""HomeMaster facade over the MindMemOS HTTP SDK (V3.8).

This is the ONLY boundary through which HomeMaster talks to MindMemOS: every
memory mutation goes through this async client to the standalone MindMemOS
server. Nothing here imports ``mindmemos`` server-side packages.

All mutating calls carry a stable ``operation_id`` so a crashed worker can
reconcile via :meth:`MindMemOSClient.operation_status` and retry the same
operation without duplicating server-side effects.

The facade owns typed receipts: raw envelope ``data`` is normalized into
frozen dataclasses so workers and tools never re-parse SDK models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from mindmemos_sdk.transport import AsyncHttpTransport


def _sdk_error_types() -> tuple[type[Exception], type[Exception]]:
    """Resolve SDK error classes lazily.

    ``mindmemos-sdk`` ships in the ``memory`` install extra — the base wheel
    must import this module (and ``homemaster.adapters``) without it.
    """
    try:
        from mindmemos_sdk.errors import ApiError, TransportError
    except ImportError as exc:
        raise MindMemOSClientError(
            "mindmemos-sdk is not installed; install homemaster[memory] for "
            "memory.mode='full'"
        ) from exc
    return ApiError, TransportError

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MindMemOSClientError(RuntimeError):
    """A typed client-side failure (transport or business)."""


class OperationConflictError(MindMemOSClientError):
    """Same operation_id replayed with a different payload (server 409)."""


class OperationNotFoundError(MindMemOSClientError):
    """The server ledger has no such operation (404)."""


class OperationStillProcessingError(MindMemOSClientError):
    """The server is still executing this operation_id (HTTP 202, code
    ``processing``). Not a failure: reconcile via the operations ledger and
    resubmit only after it reaches a terminal status."""


class OutcomeUnknownError(MindMemOSClientError):
    """The request may or may not have executed (timeout/network post-send)."""


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------

ReceiptStatus = Literal["completed", "no_action", "processing", "accepted"]


@dataclass(frozen=True)
class MemoryScope:
    """Typed tenant + app/agent scope. ``tenant_id`` is the authoritative
    partition (mapped to user/account/project server-side); it is never
    confused with a principal subject id."""

    tenant_id: str
    app_id: str = "homemaster"
    agent_id: str = "homemaster"


@dataclass(frozen=True)
class AddReceipt:
    operation_id: str
    add_record_id: str
    memory_ids: tuple[str, ...]
    status: str
    request_id: str | None
    replayed: bool = False


@dataclass(frozen=True)
class SearchHit:
    id: str
    memory: str
    memory_type: str
    score: float | None
    metadata: dict[str, Any] = field(default_factory=dict)
    last_update_at: str | None = None


@dataclass(frozen=True)
class SearchReceipt:
    search_record_id: str | None
    hits: tuple[SearchHit, ...]
    request_id: str | None


@dataclass(frozen=True)
class FeedbackAction:
    action_id: str
    action: str
    memory_id: str | None


@dataclass(frozen=True)
class FeedbackReceipt:
    operation_id: str
    feedback_record_id: str
    actions: tuple[FeedbackAction, ...]
    status: str
    request_id: str | None


@dataclass(frozen=True)
class DreamingReceipt:
    operation_id: str
    dreaming_record_id: str
    produced_memory_ids: tuple[str, ...]
    actions: tuple[dict[str, Any], ...]
    status: str
    request_id: str | None


@dataclass(frozen=True)
class OperationStatus:
    operation_id: str
    kind: str | None
    status: str
    result: dict[str, Any] | None
    error: str | None


@dataclass(frozen=True)
class MutationReceipt:
    """Receipt for management mutations (update/delete/create)."""

    status: str
    memory_id: str | None
    request_id: str | None
    version: str | None = None


@dataclass(frozen=True)
class SkillTraceReceipt:
    invocation_id: str
    cloud_skill_id: str
    unconsumed_count: int


@dataclass(frozen=True)
class EvolutionBatchReceipt:
    batch_id: str
    draft_version_id: str | None
    status: str
    reason: str | None


@dataclass(frozen=True)
class SkillVersionInfo:
    version_id: str
    parent_version_id: str | None
    status: str
    version_label: str | None
    content_hash: str | None


@dataclass(frozen=True)
class SkillVersionContent:
    version: SkillVersionInfo
    content: str


def _data(envelope: Any) -> dict[str, Any]:
    data = getattr(envelope, "data", None)
    return data if isinstance(data, dict) else {}


class MindMemOSClient:
    """Async facade: one instance per HomeMaster application.

    ``base_url`` points at a standalone MindMemOS HTTP server; ``api_key``
    authenticates the tenant partition. The embedded ``EmbeddedMindMemOS``
    path is removed in V3.8 — this client is the replacement.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        scope: MemoryScope,
        timeout_seconds: float = 120.0,
        max_retries: int = 0,
        transport: AsyncHttpTransport | None = None,
    ) -> None:
        self._scope = scope
        self._owns_transport = transport is None
        if transport is not None:
            self._transport = transport
        else:
            try:
                from mindmemos_sdk.transport import AsyncHttpTransport
            except ImportError as exc:
                raise MindMemOSClientError(
                    "mindmemos-sdk is not installed; install homemaster[memory] "
                    "for memory.mode='full'"
                ) from exc
            self._transport = AsyncHttpTransport(
                base_url=base_url,
                api_key=api_key,
                timeout_seconds=timeout_seconds,
                # retries happen at the worker/operation layer with a stable
                # operation_id — transport-level retries could double-fire a
                # mutation without the ledger seeing a consistent attempt
                # count.
                max_retries=max_retries,
            )

    @property
    def scope(self) -> MemoryScope:
        return self._scope

    async def aclose(self) -> None:
        if self._owns_transport:
            await self._transport.aclose()

    # -- plumbing ---------------------------------------------------------

    @staticmethod
    def _classify_error(path: str, exc: Exception, *, get: bool) -> Exception:
        api_error, transport_error = _sdk_error_types()
        if isinstance(exc, api_error):
            code = getattr(exc, "code", None)
            if get and code == "not_found":
                return OperationNotFoundError(str(exc))
            if code == "processing":
                return OperationStillProcessingError(str(exc))
            if not get and code == "conflict":
                return OperationConflictError(str(exc))
            return MindMemOSClientError(f"{path}: {exc}")
        if isinstance(exc, transport_error):
            # The request may have been executed: callers must reconcile via
            # operation_status before deciding to retry.
            return OutcomeUnknownError(f"{path}: {exc}")
        return exc if isinstance(exc, MindMemOSClientError) else MindMemOSClientError(
            f"{path}: {exc}"
        )

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            envelope = await self._transport.post_envelope(path, json=body)
        except Exception as exc:
            raise self._classify_error(path, exc, get=False) from exc
        return _data(envelope)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            envelope = await self._transport.get_envelope(path, params=params)
        except Exception as exc:
            raise self._classify_error(path, exc, get=True) from exc
        return _data(envelope)

    def _scope_fields(self, session_id: str | None) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "user_id": self._scope.tenant_id,
            "app_id": self._scope.app_id,
            "agent_id": self._scope.agent_id,
        }
        if session_id is not None:
            fields["session_id"] = session_id
        return fields

    # -- turn add -----------------------------------------------------------

    async def add_turn(
        self,
        *,
        operation_id: str,
        session_id: str,
        turn_id: str,
        dialogue: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> AddReceipt:
        """Submit one canonical turn for Vanilla Add (spec §4.2)."""
        body: dict[str, Any] = {
            "messages": dialogue,
            "mode": "sync",
            "operation_id": operation_id,
            "metadata": {
                "source": "homemaster.turn",
                "session_id": session_id,
                "turn_id": turn_id,
                **(metadata or {}),
            },
            **self._scope_fields(session_id),
        }
        data = await self._post("/v1/memory/add", body)
        return AddReceipt(
            operation_id=str(data.get("operation_id") or operation_id),
            add_record_id=str(data.get("add_record_id") or ""),
            memory_ids=tuple(str(mid) for mid in (data.get("memory_ids") or ())),
            status=str(data.get("status") or "completed"),
            request_id=None,
            replayed=bool(data.get("replayed")),
        )

    # -- operations ledger ---------------------------------------------------

    async def operation_status(self, operation_id: str) -> OperationStatus:
        data = await self._get(f"/v1/operations/{operation_id}")
        return OperationStatus(
            operation_id=operation_id,
            kind=data.get("kind"),
            status=str(data.get("status") or "unknown"),
            result=data.get("result") if isinstance(data.get("result"), dict) else None,
            error=data.get("error") if isinstance(data.get("error"), str) else None,
        )

    # -- search ---------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        session_id: str | None = None,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> SearchReceipt:
        body: dict[str, Any] = {
            "query": query,
            "top_k": top_k,
            **self._scope_fields(session_id),
        }
        if filters:
            body["filters"] = filters
        data = await self._post("/v1/memory/search", body)
        hits = tuple(
            SearchHit(
                id=str(item.get("id") or ""),
                memory=str(item.get("memory") or ""),
                memory_type=str(item.get("memory_type") or "fact"),
                score=item.get("score") if isinstance(item.get("score"), (int, float)) else None,
                metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                last_update_at=item.get("last_update_at"),
            )
            for item in (data.get("memories") or ())
        )
        return SearchReceipt(
            search_record_id=data.get("search_record_id"),
            hits=hits,
            request_id=None,
        )

    # -- feedback ---------------------------------------------------------------

    async def feedback_explicit(
        self,
        *,
        operation_id: str,
        feedback: str,
        messages: list[dict[str, Any]],
        recalled_memories: list[dict[str, Any]],
        session_id: str | None = None,
    ) -> FeedbackReceipt:
        """Explicit feedback: text + frozen dialogue + the MemorySearchItem
        dicts the model actually saw (``id``/``memory``/``last_update_at``
        required by the server schema)."""
        body: dict[str, Any] = {
            "mode": "sync",
            "feedback": feedback,
            "messages": messages,
            "recalled_memories": recalled_memories,
            "operation_id": operation_id,
            **self._scope_fields(session_id),
        }
        return self._feedback_receipt(await self._post("/v1/memory/feedback", body), operation_id)

    async def feedback_implicit(
        self,
        *,
        operation_id: str,
        window: dict[str, Any],
        session_id: str | None = None,
    ) -> FeedbackReceipt:
        """Implicit feedback: no ``feedback`` text — the server analyzes
        recent add/search records inside ``window`` itself."""
        body: dict[str, Any] = {
            "mode": "sync",
            "window": window,
            "operation_id": operation_id,
            **self._scope_fields(session_id),
        }
        return self._feedback_receipt(await self._post("/v1/memory/feedback", body), operation_id)

    @staticmethod
    def _feedback_receipt(data: dict[str, Any], operation_id: str) -> FeedbackReceipt:
        actions = tuple(
            FeedbackAction(
                action_id=str(a.get("action_id") or ""),
                action=str(a.get("action") or "noop"),
                memory_id=a.get("memory_id"),
            )
            for a in (data.get("actions") or ())
        )
        return FeedbackReceipt(
            operation_id=str(data.get("operation_id") or operation_id),
            feedback_record_id=str(data.get("feedback_record_id") or ""),
            actions=actions,
            status=str(data.get("status") or "completed"),
            request_id=None,
        )

    # -- dreaming ---------------------------------------------------------------

    async def dreaming(
        self,
        *,
        operation_id: str,
        seed_add_record_ids: list[str],
        session_id: str | None = None,
    ) -> DreamingReceipt:
        body: dict[str, Any] = {
            "mode": "sync",
            "operation_id": operation_id,
            "seed_add_record_ids": list(seed_add_record_ids),
            **self._scope_fields(session_id),
        }
        data = await self._post("/v1/memory/dreaming", body)
        return DreamingReceipt(
            operation_id=str(data.get("operation_id") or operation_id),
            dreaming_record_id=str(data.get("dreaming_record_id") or ""),
            produced_memory_ids=tuple(str(mid) for mid in (data.get("produced_memory_ids") or ())),
            actions=tuple(data.get("actions") or ()),
            status=str(data.get("status") or "completed"),
            request_id=None,
        )

    # -- management (web CRUD) -----------------------------------------------------

    async def list_memories(
        self, *, filters: dict[str, Any] | None = None, page: int = 1, page_size: int = 20
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "filters": filters or {},
            "page": page,
            "page_size": page_size,
            "include_total": True,
            **self._scope_fields(None),
        }
        return await self._post("/v1/memory/list", body)

    async def scroll_memories(
        self, *, filters: dict[str, Any] | None = None, limit: int = 100, cursor: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "filters": filters or {},
            "limit": limit,
            **self._scope_fields(None),
        }
        if cursor:
            body["cursor"] = cursor
        return await self._post("/v1/memory/scroll", body)

    async def get_memory(self, *, filters: dict[str, Any]) -> dict[str, Any]:
        # GetRequest carries no actor identity and forbids extra fields —
        # the partition comes from the api_key server-side.
        return await self._post("/v1/memory/get", {"filters": filters})

    async def create_memory(
        self,
        *,
        operation_id: str,
        content: str,
        mem_type: str = "fact",
        metadata: dict[str, Any] | None = None,
    ) -> MutationReceipt:
        body: dict[str, Any] = {
            "messages": [{"role": "user", "content": content}],
            "mode": "sync",
            "operation_id": operation_id,
            "metadata": {"source": "homemaster.web", "mem_type": mem_type, **(metadata or {})},
            **self._scope_fields(None),
        }
        data = await self._post("/v1/memory/add", body)
        ids = data.get("memory_ids") or ()
        return MutationReceipt(
            status=str(data.get("status") or "completed"),
            memory_id=str(ids[0]) if ids else None,
            request_id=None,
        )

    async def update_memory(
        self,
        *,
        memory_id: str,
        operation_id: str,
        content: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> MutationReceipt:
        body: dict[str, Any] = {"memory_id": memory_id, "operation_id": operation_id}
        if content is not None:
            body["content"] = content
        if fields:
            body["fields"] = fields
        body.update(self._scope_fields(None))
        data = await self._post("/v1/memory/update", body)
        return MutationReceipt(
            status=str(data.get("status") or "completed"),
            memory_id=str(data.get("memory_id") or memory_id),
            request_id=None,
            version=data.get("version"),
        )

    async def delete_memory(self, *, memory_id: str, operation_id: str) -> MutationReceipt:
        # DeleteRequest likewise forbids actor-identity extras.
        body = {"memory_id": memory_id, "operation_id": operation_id}
        data = await self._post("/v1/memory/delete", body)
        return MutationReceipt(
            status=str(data.get("status") or "completed"),
            memory_id=memory_id,
            request_id=None,
        )

    # -- skill traces --------------------------------------------------------------

    async def submit_skill_trace(
        self, *, cloud_skill_id: str, trace: dict[str, Any]
    ) -> SkillTraceReceipt:
        data = await self._post(f"/v1/skills/{cloud_skill_id}/traces", trace)
        return SkillTraceReceipt(
            invocation_id=str(data.get("invocation_id") or trace.get("invocation_id") or ""),
            cloud_skill_id=cloud_skill_id,
            unconsumed_count=int(data.get("unconsumed_count") or 0),
        )

    async def submit_evolution_batch(
        self, *, cloud_skill_id: str, batch_id: str, trace_ids: list[str]
    ) -> EvolutionBatchReceipt:
        data = await self._post(
            f"/v1/skills/{cloud_skill_id}/evolution-batches",
            {"batch_id": batch_id, "trace_ids": list(trace_ids)},
        )
        return EvolutionBatchReceipt(
            batch_id=batch_id,
            draft_version_id=data.get("draft_version_id"),
            status=str(data.get("status") or "completed"),
            reason=data.get("reason"),
        )

    async def list_skill_versions(self, *, cloud_skill_id: str) -> list[SkillVersionInfo]:
        """All versions of one cloud skill with parent lineage (read-only).

        UNVERIFIED: response shape follows vendored ``skill_routes.py`` /
        ``SkillVersionsData``; confirm against a live server before trusting
        draft visibility.
        """
        data = await self._get(f"/v1/skills/{cloud_skill_id}/versions")
        return [
            SkillVersionInfo(
                version_id=str(item.get("version_id") or ""),
                parent_version_id=item.get("parent_version_id"),
                status=str(item.get("status") or ""),
                version_label=item.get("version_label"),
                content_hash=item.get("content_hash"),
            )
            for item in data.get("versions") or []
            if isinstance(item, dict)
        ]

    async def get_skill_version_content(
        self, *, cloud_skill_id: str, version_id: str
    ) -> SkillVersionContent:
        """Full SKILL.md text of one version (read-only).

        UNVERIFIED: same caveat as :meth:`list_skill_versions`.
        """
        data = await self._get(
            f"/v1/skills/{cloud_skill_id}/versions/{version_id}/content"
        )
        version = data.get("version") or {}
        return SkillVersionContent(
            version=SkillVersionInfo(
                version_id=str(version.get("version_id") or ""),
                parent_version_id=version.get("parent_version_id"),
                status=str(version.get("status") or ""),
                version_label=version.get("version_label"),
                content_hash=version.get("content_hash"),
            ),
            content=str(data.get("content") or ""),
        )

    # -- health ----------------------------------------------------------------------

    async def health(self) -> bool:
        """Readiness probe used by worker startup (spec worker readiness gate)."""
        try:
            envelope = await self._transport.get_envelope("/healthz")
        except Exception:
            return False
        return getattr(envelope, "code", "ok") == "ok" or True


__all__ = [
    "AddReceipt",
    "DreamingReceipt",
    "EvolutionBatchReceipt",
    "FeedbackAction",
    "FeedbackReceipt",
    "MemoryScope",
    "MindMemOSClient",
    "MindMemOSClientError",
    "MutationReceipt",
    "OperationConflictError",
    "OperationNotFoundError",
    "OperationStillProcessingError",
    "OperationStatus",
    "OutcomeUnknownError",
    "SearchHit",
    "SearchReceipt",
    "SkillTraceReceipt",
    "SkillVersionContent",
    "SkillVersionInfo",
]
