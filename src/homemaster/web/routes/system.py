"""Misc/system routes: meta, health, doctor, and the artifact download."""

from __future__ import annotations

import hashlib
import importlib.metadata

from fastapi import FastAPI, Response

from homemaster import __version__
from homemaster.artifacts.tool_output_store import ArtifactStoreError
from homemaster.web._shared import _error, _session_ids
from homemaster.web.context import WebAppContext


def register(app: FastAPI, ctx: WebAppContext) -> None:
    application = ctx.application
    config = ctx.config
    environment_label = ctx.environment_label

    @app.get("/api/meta")
    async def meta() -> object:
        try:
            version = importlib.metadata.version("homemaster")
        except importlib.metadata.PackageNotFoundError:
            version = "0.0.0"
        memory_mode = getattr(getattr(config, "memory", None), "mode", None)
        return {
            "version": version,
            "memory_mode": memory_mode if isinstance(memory_mode, str) else "files",
            "environment": environment_label,
        }

    @app.get("/api/health")
    async def health() -> object:
        return {"status": "ok", "version": __version__}

    @app.get("/api/doctor")
    def doctor(live: bool = False, alfworld: bool = False) -> object:
        # Sync ``def`` on purpose: run_doctor drives subprocesses and
        # ``asyncio.run`` internally, so it must stay off the event loop —
        # FastAPI sends sync endpoints to the threadpool automatically.
        from homemaster.cli.doctor import run_doctor

        report = run_doctor(
            live=live,
            alfworld=alfworld,
            config_path=getattr(config, "config_path", None),
        )
        return report.model_dump(mode="json")

    @app.get("/api/artifacts/{artifact_handle}")
    async def download_artifact(
        artifact_handle: str,
        session_id: str,
        run_id: str,
    ) -> object:
        publisher = getattr(application, "artifact_publisher", None)
        store = getattr(publisher, "store", None)
        try:
            if store is None or session_id not in _session_ids(application.session_manager):
                raise ArtifactStoreError("artifact store unavailable")
            content = store.read(
                artifact_handle,
                tenant_id="local",
                session_id=session_id,
                run_id=run_id,
            )
        except (ArtifactStoreError, ValueError):
            return _error(
                404,
                "artifact_not_found",
                "The artifact is unavailable or expired.",
                retryable=False,
            )
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"X-Content-SHA256": hashlib.sha256(content).hexdigest()},
        )


__all__ = ["register"]
