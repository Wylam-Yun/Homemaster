"""Dedicated skill invocation-trace and evolution-batch orchestration (V3.8).

These stores back the explicit lifecycle endpoints
(``POST /v1/skills/{cloud_skill_id}/traces`` and
``POST /v1/skills/{cloud_skill_id}/evolution-batches``) — a separate,
client-driven trace surface that does NOT reuse the add-record
``skill_bindings`` path used by threshold-based self-evolution.

Both stores are project + skill scoped and idempotent:

- ``skill_traces`` is keyed by ``invocation_id``; a resubmission with an
  identical payload replays the stored response, a mismatched payload
  conflicts (409).
- ``skill_evolution_batches`` is keyed by ``batch_id``; a resubmission with the
  same ``trace_ids`` replays the stored result verbatim.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ...errors import ConflictError
from ...infra.db import DatabaseClients, resolve_database_clients
from ...infra.db.collections.skill_lifecycle import (
    SkillEvolutionBatchRepository,
    SkillTraceRepository,
    skill_batch_point_id,
    skill_trace_point_id,
)
from ...infra.db.models import SkillEvolutionBatchPoint, SkillTracePoint
from ...logging import get_logger
from ...services.operations import canonical_request_hash
from ...typing import SkillTrace, SkillVersion
from .version_store import SkillVersionStore, get_skill_version_store

logger = get_logger(__name__)


class SkillTraceStore:
    """Orchestration over ``skill_traces`` + ``skill_evolution_batches``.

    Repositories, the version store, and the evolver are resolved lazily from
    process globals so the store survives config resets; every dependency is
    injectable for tests.
    """

    def __init__(
        self,
        *,
        clients: DatabaseClients | None = None,
        trace_repo: SkillTraceRepository | None = None,
        batch_repo: SkillEvolutionBatchRepository | None = None,
        store: SkillVersionStore | None = None,
        evolver: Any = None,
    ) -> None:
        self._clients = resolve_database_clients(clients)
        self._trace_repo = trace_repo
        self._batch_repo = batch_repo
        self._store = store
        self._evolver = evolver

    @property
    def _traces(self) -> SkillTraceRepository:
        return self._trace_repo if self._trace_repo is not None else self._clients.skill_traces

    @property
    def _batches(self) -> SkillEvolutionBatchRepository:
        return self._batch_repo if self._batch_repo is not None else self._clients.skill_batches

    @property
    def _version_store(self) -> SkillVersionStore:
        return self._store if self._store is not None else get_skill_version_store()

    @property
    def _skill_evolver(self) -> Any:
        if self._evolver is None:
            from .evolution import get_skill_evolver

            self._evolver = get_skill_evolver()
        return self._evolver

    # -- traces ----------------------------------------------------------------

    async def submit_trace(
        self,
        *,
        project_id: str,
        cloud_skill_id: str,
        trace: SkillTrace,
    ) -> dict[str, Any]:
        """Seal one invocation trace; idempotent on ``invocation_id``.

        A resubmission with the same payload replays the stored response
        (including the originally reported ``unconsumed_count``); a resubmission
        with different trace content is rejected with ``ConflictError`` (409).
        """

        fingerprint = canonical_request_hash(
            trace.model_dump(
                mode="json", exclude={"request_fingerprint", "created_at", "status", "consumed_by_batch_id"}
            )
        )
        existing = await self._traces.get(project_id, cloud_skill_id, trace.invocation_id)
        if existing is not None:
            stored = existing.payload or {}
            stored_fp = stored.get("request_fingerprint")
            if stored_fp and stored_fp != fingerprint:
                raise ConflictError(f"invocation_id {trace.invocation_id!r} already sealed with different content")
            return {
                "invocation_id": trace.invocation_id,
                "cloud_skill_id": cloud_skill_id,
                "status": "sealed",
                "unconsumed_count": int(stored.get("submitted_unconsumed_count") or 0),
            }

        unconsumed = await self._traces.count_unconsumed(project_id, cloud_skill_id)
        now = datetime.now(UTC)
        payload = trace.model_dump(mode="json")
        payload["status"] = "sealed"
        payload["request_fingerprint"] = fingerprint
        payload["submitted_unconsumed_count"] = unconsumed + 1
        payload["created_at"] = now.isoformat()
        await self._traces.upsert(
            SkillTracePoint(
                trace_point_id=skill_trace_point_id(project_id, cloud_skill_id, trace.invocation_id),
                payload=payload,
            )
        )
        return {
            "invocation_id": trace.invocation_id,
            "cloud_skill_id": cloud_skill_id,
            "status": "sealed",
            "unconsumed_count": unconsumed + 1,
        }

    async def get_trace(
        self,
        *,
        project_id: str,
        cloud_skill_id: str,
        invocation_id: str,
    ) -> SkillTrace | None:
        """Return one sealed trace, or ``None`` when unknown."""

        record = await self._traces.get(project_id, cloud_skill_id, invocation_id)
        if record is None:
            return None
        payload = dict(record.payload or {})
        payload.setdefault("invocation_id", invocation_id)
        payload.setdefault("cloud_skill_id", cloud_skill_id)
        payload.setdefault("project_id", project_id)
        return SkillTrace.model_validate(payload)

    # -- evolution batches -------------------------------------------------------

    async def run_evolution_batch(
        self,
        *,
        project_id: str,
        cloud_skill_id: str,
        batch_id: str,
        trace_ids: list[str],
    ) -> dict[str, Any]:
        """Claim ``batch_id`` over exactly ``trace_ids`` and mint a draft version.

        Every listed trace must exist as a sealed trace for ``cloud_skill_id``;
        unknown or mismatched ids raise ``ConflictError`` (409). A resubmission
        of the same ``batch_id`` + ``trace_ids`` replays the stored result.
        The produced version is always ``draft`` — never auto-activated.
        """

        existing = await self._batches.get(project_id, cloud_skill_id, batch_id)
        if existing is not None:
            payload = existing.payload or {}
            stored_ids = [str(v) for v in payload.get("trace_ids") or []]
            if stored_ids != [str(v) for v in trace_ids]:
                raise ConflictError(f"batch_id {batch_id!r} already claimed with a different trace set")
            return {
                "batch_id": batch_id,
                "draft_version_id": payload.get("draft_version_id"),
                "status": payload.get("status") or "completed",
                "reason": payload.get("reason"),
            }

        records = await self._traces.get_many(project_id, cloud_skill_id, trace_ids)
        found: dict[str, dict[str, Any]] = {}
        for record in records:
            payload = record.payload or {}
            iid = str(payload.get("invocation_id") or "")
            if iid:
                found[iid] = payload
        missing = [tid for tid in trace_ids if tid not in found]
        invalid = [
            tid
            for tid, payload in found.items()
            if str(payload.get("cloud_skill_id") or "") != cloud_skill_id
            or str(payload.get("status") or "") != "sealed"
        ]
        if missing or invalid:
            problems = [f"unknown:{tid}" for tid in missing] + [f"invalid:{tid}" for tid in invalid]
            raise ConflictError(
                f"evolution batch {batch_id!r} references traces that are not sealed for "
                f"this skill: {', '.join(problems)}"
            )

        now = datetime.now(UTC)
        await self._batches.upsert(
            SkillEvolutionBatchPoint(
                batch_point_id=skill_batch_point_id(project_id, cloud_skill_id, batch_id),
                payload={
                    "batch_id": batch_id,
                    "project_id": project_id,
                    "cloud_skill_id": cloud_skill_id,
                    "trace_ids": list(trace_ids),
                    "status": "processing",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                },
            )
        )

        traces = [SkillTrace.model_validate(found[tid]) for tid in trace_ids]
        try:
            version, reason = await self._skill_evolver.evolve_traces(
                project_id=project_id,
                cloud_skill_id=cloud_skill_id,
                traces=traces,
                batch_id=batch_id,
            )
        except Exception as exc:
            await self._batches.patch(
                project_id,
                cloud_skill_id,
                batch_id,
                {
                    "status": "failed",
                    "error": str(exc)[:2000],
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            )
            raise

        status = "completed" if version is not None else "no_action"
        draft_version_id = version.version_id if isinstance(version, SkillVersion) else None
        await self._batches.patch(
            project_id,
            cloud_skill_id,
            batch_id,
            {
                "status": status,
                "draft_version_id": draft_version_id,
                "reason": reason,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        if draft_version_id is not None:
            for tid in trace_ids:
                try:
                    await self._traces.patch(
                        project_id,
                        cloud_skill_id,
                        tid,
                        {"consumed_by_batch_id": batch_id, "status": "consumed"},
                    )
                except Exception:
                    logger.warning(
                        "skill trace consume-mark failed",
                        invocation_id=tid,
                        batch_id=batch_id,
                        exc_info=True,
                    )
        return {
            "batch_id": batch_id,
            "draft_version_id": draft_version_id,
            "status": status,
            "reason": reason,
        }


_trace_store: SkillTraceStore | None = None


def get_skill_trace_store() -> SkillTraceStore:
    """Process-global skill trace/batch store."""

    global _trace_store
    if _trace_store is None:
        _trace_store = SkillTraceStore()
    return _trace_store
