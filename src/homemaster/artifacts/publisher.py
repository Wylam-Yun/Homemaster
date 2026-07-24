"""Gateway-safe resolution of opaque tool artifacts."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from homemaster.artifacts.tool_output_store import ArtifactStoreError, ToolOutputStore
from homemaster.channels.contracts import OutboundArtifactRef
from homemaster.tools.contracts import ToolExecutionResult


@dataclass(frozen=True)
class ResolvedArtifact:
    content: bytes
    filename: str
    media_type: str
    content_sha256: str


class ToolOutputArtifactResolver:
    def __init__(self, store: ToolOutputStore) -> None:
        self.store = store

    def resolve(
        self,
        ref: OutboundArtifactRef,
        *,
        tenant_id: str,
        session_id: str,
    ) -> ResolvedArtifact:
        content = self.store.read(
            ref.artifact_handle,
            tenant_id=tenant_id,
            session_id=session_id,
            run_id=ref.run_id,
        )
        digest = hashlib.sha256(content).hexdigest()
        if digest != ref.content_sha256:
            raise ArtifactStoreError("outbound artifact hash does not match authoritative store")
        return ResolvedArtifact(
            content=content,
            filename=ref.filename,
            media_type=ref.media_type,
            content_sha256=digest,
        )


class ArtifactPublisher:
    """Persist result media and return opaque refs for the Gateway projection."""

    def __init__(self, store: ToolOutputStore) -> None:
        self.store = store

    def publish(
        self,
        result: ToolExecutionResult,
        *,
        tenant_id: str,
        session_id: str,
        run_id: str,
    ) -> tuple[dict[str, str], ...]:
        artifacts: list[dict[str, str]] = []
        for index, image in enumerate(result.images):
            extension = image.media_type.removeprefix("image/").split("+", 1)[0] or "bin"
            artifacts.append(
                self._store(
                    tenant_id=tenant_id,
                    session_id=session_id,
                    run_id=run_id,
                    content=base64.b64decode(image.data_base64, validate=True),
                    filename=f"image-{index}.{extension}",
                    media_type=image.media_type,
                    content_sha256=image.content_sha256,
                )
            )
        for attachment in result.attachments:
            artifacts.append(
                self._store(
                    tenant_id=tenant_id,
                    session_id=session_id,
                    run_id=run_id,
                    content=base64.b64decode(attachment.data_base64, validate=True),
                    filename=attachment.filename,
                    media_type=attachment.media_type,
                    content_sha256=attachment.content_sha256,
                )
            )
        if not artifacts:
            return ()
        return tuple(artifacts)

    def _store(
        self,
        *,
        tenant_id: str,
        session_id: str,
        run_id: str,
        content: bytes,
        filename: str,
        media_type: str,
        content_sha256: str,
    ) -> dict[str, str]:
        stored = self.store.write(
            tenant_id=tenant_id,
            session_id=session_id,
            run_id=run_id,
            content=content,
            media_type=media_type,
        )
        if stored.content_sha256 != content_sha256:
            raise ArtifactStoreError("published artifact hash changed before storage")
        return {
            "artifact_handle": stored.handle,
            "run_id": run_id,
            "filename": filename,
            "media_type": media_type,
            "content_sha256": stored.content_sha256,
        }


__all__ = ["ArtifactPublisher", "ResolvedArtifact", "ToolOutputArtifactResolver"]
