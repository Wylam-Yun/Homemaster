"""Skill HTTP business logic (design §5.2–§5.4).

Thin adapter between the ``/v1/skills/*`` routes and the
:class:`~mindmemos.pipelines.skill.SkillVersionStore` orchestration. It assembles
nothing of its own beyond translating the security-only ``AuthContext`` into a
``project_id`` and mapping domain skill errors to HTTP errors; the version-store
owns dedup, lineage and rebind.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ...errors import (
    BadRequestError,
    ConflictError,
    ResourceNotFoundError,
    SkillBundleError,
    SkillContentNotFoundError,
    SkillNotFoundError,
    SkillVersionNotFoundError,
)
from ...infra.kafka import get_producer
from ...logging import get_logger, traced
from ...pipelines import create_pipeline
from ...pipelines.skill import (
    SKILL_EVOLVE_TOPIC,
    SkillEvolvePipeline,
    SkillTraceStore,
    SkillVersionStore,
    get_skill_evolver,
    get_skill_trace_store,
    get_skill_version_store,
)
from ...provider_bindings import provider_config_context
from ...typing import SkillEvolveResult, SkillTrace
from ..deps import annotate_request_trace
from ..mappers import to_memory_request_context
from ..schemas import AuthContext
from ..skill_schemas import (
    EvolutionBatchData,
    EvolutionBatchRequest,
    SkillContentData,
    SkillEvolveData,
    SkillEvolveRequest,
    SkillListData,
    SkillRegisterData,
    SkillRegisterRequest,
    SkillSyncData,
    SkillSyncRequest,
    SkillTraceData,
    SkillTraceSubmitData,
    SkillTraceSubmitRequest,
    SkillVersionsData,
)

logger = get_logger(__name__)


class SkillService:
    """Stateless facade routing skill endpoints to the version store."""

    def __init__(
        self,
        *,
        store: SkillVersionStore | None = None,
        evolver: SkillEvolvePipeline | None = None,
        trace_store: SkillTraceStore | None = None,
    ) -> None:
        self._store = store or get_skill_version_store()
        # Resolved lazily from config on first evolve(), so building the service
        # for the read/write endpoints never requires the evolve pipeline config.
        self._evolver = evolver
        self._trace_store = trace_store

    @property
    def trace_store(self) -> SkillTraceStore:
        """Return the dedicated trace/batch store (V3.8)."""

        if self._trace_store is None:
            self._trace_store = get_skill_trace_store()
        return self._trace_store

    @property
    def evolver(self) -> SkillEvolvePipeline:
        if self._evolver is None:
            self._evolver = get_skill_evolver()
        return self._evolver

    @traced("skill_service.register")
    async def register(self, auth: AuthContext, request: SkillRegisterRequest) -> SkillRegisterData:
        """Register a skill version idempotently (design §5.2)."""

        annotate_request_trace(auth)
        try:
            version = await self._store.register(
                project_id=auth.project_id,
                name=request.name,
                content=request.content,
                version_label=request.version_label,
                parent_version_id=request.parent_version_id,
            )
        except SkillBundleError as exc:
            raise BadRequestError(str(exc), code="skill.invalid_bundle") from exc
        except SkillVersionNotFoundError as exc:
            raise ResourceNotFoundError(str(exc), code="skill.parent_not_found") from exc
        return SkillRegisterData(
            cloud_skill_id=version.cloud_skill_id,
            version_id=version.version_id,
            version_label=version.version_label,
            content_hash=version.content_hash,
            status=version.status,
        )

    @traced("skill_service.list")
    async def list_skills(self, auth: AuthContext) -> SkillListData:
        """List project-managed skills (design §5.4)."""

        annotate_request_trace(auth)
        skills = await self._store.list_skills(project_id=auth.project_id)
        return SkillListData(skills=skills)

    @traced("skill_service.get")
    async def get_skill(self, auth: AuthContext, cloud_skill_id: str):
        """Return skill metadata plus published head (design §5.4)."""

        annotate_request_trace(auth)
        try:
            return await self._store.get_skill(project_id=auth.project_id, cloud_skill_id=cloud_skill_id)
        except SkillNotFoundError as exc:
            raise ResourceNotFoundError(str(exc), code="skill.not_found") from exc

    @traced("skill_service.versions")
    async def versions(
        self,
        auth: AuthContext,
        cloud_skill_id: str,
        since: datetime | None = None,
    ) -> SkillVersionsData:
        """Return incremental version metadata without content (design §5.4)."""

        annotate_request_trace(auth)
        try:
            versions = await self._store.versions_since(
                project_id=auth.project_id,
                cloud_skill_id=cloud_skill_id,
                since=since,
            )
        except SkillNotFoundError as exc:
            raise ResourceNotFoundError(str(exc), code="skill.not_found") from exc
        return SkillVersionsData(versions=versions)

    @traced("skill_service.content")
    async def content(self, auth: AuthContext, cloud_skill_id: str, version_id: str) -> SkillContentData:
        """Return canonical bundle content for one version (design §5.4)."""

        annotate_request_trace(auth)
        try:
            content = await self._store.get_content(
                project_id=auth.project_id,
                cloud_skill_id=cloud_skill_id,
                version_id=version_id,
            )
        except SkillVersionNotFoundError as exc:
            raise ResourceNotFoundError(str(exc), code="skill.version_not_found") from exc
        except SkillContentNotFoundError as exc:
            raise ResourceNotFoundError(str(exc), code="skill.content_not_found") from exc
        return SkillContentData(version=content.version, content=content.content)

    @traced("skill_service.delete")
    async def delete(self, auth: AuthContext, cloud_skill_id: str) -> None:
        """Unmanage a cloud skill (design §5.4)."""

        annotate_request_trace(auth)
        try:
            await self._store.delete_skill(project_id=auth.project_id, cloud_skill_id=cloud_skill_id)
        except SkillNotFoundError as exc:
            raise ResourceNotFoundError(str(exc), code="skill.not_found") from exc

    @traced("skill_service.evolve")
    async def evolve(self, auth: AuthContext, request: SkillEvolveRequest) -> SkillEvolveData:
        """Run one skill self-evolution pass for a cloud skill.

        Returns the result DTO: when the pending trajectory count is below the
        threshold ``evolved`` is false and the caller learns how many more are
        needed; otherwise it carries the freshly minted version id(s).
        """
        annotate_request_trace(auth)
        context = to_memory_request_context(auth, request)

        if request.mode == "async":
            await get_producer().send(
                SKILL_EVOLVE_TOPIC,
                value={
                    "request_id": auth.request_id,
                    "account_id": auth.account_id,
                    "project_id": auth.project_id,
                    "cloud_skill_id": request.cloud_skill_id,
                    "context": context.model_dump(mode="json"),
                    "submitted_at": datetime.now(UTC).isoformat(),
                },
                dispatch_key=f"{auth.project_id}:{request.cloud_skill_id}",
            )
            return SkillEvolveResult(
                cloud_skill_id=request.cloud_skill_id,
                status="queued",
                evolved=False,
                pending_count=0,
                threshold=0,
            )
        config_context = await provider_config_context(context)
        with config_context:
            try:
                return await self.evolver.evolve(
                    project_id=auth.project_id,
                    cloud_skill_id=request.cloud_skill_id,
                )
            except SkillNotFoundError as exc:
                raise ResourceNotFoundError(str(exc), code="skill.not_found") from exc

    @traced("skill_service.submit_trace")
    async def submit_trace(
        self,
        auth: AuthContext,
        cloud_skill_id: str,
        request: SkillTraceSubmitRequest,
    ) -> SkillTraceSubmitData:
        """Seal one dedicated invocation trace (V3.8).

        Validates the score/outcome contract (``success`` scores land in
        ``[0.5, 1]``, ``failure`` scores in ``[0, 0.5]``) and that the cloud
        skill exists; the store handles ``invocation_id`` idempotency (identical
        resubmits replay, conflicting content 409s).
        """

        annotate_request_trace(auth)
        self._validate_trace_score(request)
        try:
            await self._store.get_skill(project_id=auth.project_id, cloud_skill_id=cloud_skill_id)
        except SkillNotFoundError as exc:
            raise ResourceNotFoundError(str(exc), code="skill.not_found") from exc
        trace = SkillTrace(
            invocation_id=request.invocation_id,
            project_id=auth.project_id,
            cloud_skill_id=cloud_skill_id,
            skill_version_id=request.skill_version_id,
            skill_content_hash=request.skill_content_hash,
            task_input=request.task_input,
            tool_steps=request.tool_steps,
            agent_completion=request.agent_completion,
            outcome=request.outcome,
            score=request.score,
        )
        try:
            result = await self.trace_store.submit_trace(
                project_id=auth.project_id,
                cloud_skill_id=cloud_skill_id,
                trace=trace,
            )
        except ConflictError:
            raise
        return SkillTraceSubmitData(
            invocation_id=result["invocation_id"],
            cloud_skill_id=result["cloud_skill_id"],
            status=result["status"],
            unconsumed_count=int(result["unconsumed_count"]),
        )

    @traced("skill_service.get_trace")
    async def get_trace(
        self,
        auth: AuthContext,
        cloud_skill_id: str,
        invocation_id: str,
    ) -> SkillTraceData:
        """Return one sealed trace by ``invocation_id`` (V3.8)."""

        annotate_request_trace(auth)
        trace = await self.trace_store.get_trace(
            project_id=auth.project_id,
            cloud_skill_id=cloud_skill_id,
            invocation_id=invocation_id,
        )
        if trace is None:
            raise ResourceNotFoundError("skill trace not found", code="not_found")
        return SkillTraceData(
            invocation_id=trace.invocation_id,
            cloud_skill_id=trace.cloud_skill_id,
            skill_version_id=trace.skill_version_id,
            skill_content_hash=trace.skill_content_hash,
            task_input=trace.task_input,
            tool_steps=trace.tool_steps,
            agent_completion=trace.agent_completion,
            outcome=trace.outcome,
            score=trace.score,
            status=trace.status,
            consumed_by_batch_id=trace.consumed_by_batch_id,
            created_at=trace.created_at,
        )

    @traced("skill_service.evolution_batch")
    async def evolution_batch(
        self,
        auth: AuthContext,
        cloud_skill_id: str,
        request: EvolutionBatchRequest,
    ) -> EvolutionBatchData:
        """Run one explicit evolution batch over sealed traces (V3.8).

        Claims ``batch_id`` (409 on reuse with a different trace set), verifies
        every ``trace_ids`` entry is a sealed trace for this skill (409 on
        unknown/mismatched ids), then runs the existing evolver against exactly
        those traces — minting a draft-only version.
        """

        annotate_request_trace(auth)
        try:
            await self._store.get_skill(project_id=auth.project_id, cloud_skill_id=cloud_skill_id)
        except SkillNotFoundError as exc:
            raise ResourceNotFoundError(str(exc), code="skill.not_found") from exc
        context = to_memory_request_context(auth)
        config_context = await provider_config_context(context)
        with config_context:
            try:
                result = await self.trace_store.run_evolution_batch(
                    project_id=auth.project_id,
                    cloud_skill_id=cloud_skill_id,
                    batch_id=request.batch_id,
                    trace_ids=list(request.trace_ids),
                )
            except SkillNotFoundError as exc:
                raise ResourceNotFoundError(str(exc), code="skill.not_found") from exc
        return EvolutionBatchData(
            batch_id=result["batch_id"],
            draft_version_id=result.get("draft_version_id"),
            status=result["status"],
            reason=result.get("reason"),
        )

    @staticmethod
    def _validate_trace_score(request: SkillTraceSubmitRequest) -> None:
        """Enforce the score/outcome contract (V3.8 §trace validation)."""

        score = request.score
        if request.outcome == "success" and not 0.5 <= score <= 1.0:
            raise BadRequestError(
                "score must be within [0.5, 1.0] for outcome=success",
                code="skill_trace.invalid_score",
            )
        if request.outcome == "failure" and not 0.0 <= score <= 0.5:
            raise BadRequestError(
                "score must be within [0.0, 0.5] for outcome=failure",
                code="skill_trace.invalid_score",
            )

    @traced("skill_service.sync")
    async def sync(self, auth: AuthContext, request: SkillSyncRequest) -> SkillSyncData:
        """Compare local versions with published cloud heads (design §5.3)."""

        annotate_request_trace(auth)
        try:
            results = await self._store.sync(project_id=auth.project_id, items=request.root)
        except SkillNotFoundError as exc:
            raise ResourceNotFoundError(str(exc), code="skill.not_found") from exc
        return SkillSyncData(results=results)


_service: SkillService | None = None
_service_key: str | None = None


def get_skill_service() -> SkillService:
    """Process-global skill service singleton, used as a FastAPI dependency.

    The skill-evolve algorithm version is selected by
    ``get_config().pipelines["skill_evolve"]``; the service is rebuilt if that
    config value changes (mirrors :func:`get_memory_service`).
    """

    global _service, _service_key
    from ...config import get_config

    evolve_name = get_config().pipelines["skill_evolve"]
    if _service is None or _service_key != evolve_name:
        _service = SkillService(evolver=create_pipeline(type="skill_evolve", name=evolve_name))
        _service_key = evolve_name
    return _service
