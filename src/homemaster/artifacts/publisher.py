"""Gateway-safe resolution of opaque tool artifacts."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from homemaster.artifacts.tool_output_store import ArtifactStoreError, ToolOutputStore
from homemaster.channels.contracts import OutboundArtifactRef
from homemaster.tools.base import ToolResult
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
        result: ToolExecutionResult | ToolResult,
        *,
        tenant_id: str,
        session_id: str,
        run_id: str,
    ) -> tuple[dict[str, str], ...]:
        artifacts: list[dict[str, str]] = []
        images = (
            result.metadata.get("images", [])
            if isinstance(result, ToolResult)
            else result.images
        )
        attachments = (
            result.metadata.get("attachments", [])
            if isinstance(result, ToolResult)
            else result.attachments
        )
        for index, image in enumerate(images):
            media_type = _field(image, "media_type")
            data_base64 = _field(image, "data_base64")
            content_sha256 = _field(image, "content_sha256")
            extension = media_type.removeprefix("image/").split("+", 1)[0] or "bin"
            artifacts.append(
                self._store(
                    tenant_id=tenant_id,
                    session_id=session_id,
                    run_id=run_id,
                    content=base64.b64decode(data_base64, validate=True),
                    filename=f"image-{index}.{extension}",
                    media_type=media_type,
                    content_sha256=content_sha256,
                )
            )
        for attachment in attachments:
            data_base64 = _field(attachment, "data_base64")
            artifacts.append(
                self._store(
                    tenant_id=tenant_id,
                    session_id=session_id,
                    run_id=run_id,
                    content=base64.b64decode(data_base64, validate=True),
                    filename=_field(attachment, "filename"),
                    media_type=_field(attachment, "media_type"),
                    content_sha256=_field(attachment, "content_sha256"),
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


def _field(value: object, name: str) -> str:
    item = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    if not isinstance(item, str) or not item:
        raise ArtifactStoreError(f"tool artifact is missing {name}")
    return item


__all__ = ["ArtifactPublisher", "ResolvedArtifact", "ToolOutputArtifactResolver"]
