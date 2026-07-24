"""Partitioned raw tool-output storage with opaque handles and exact ACL checks."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

_HANDLE_PREFIX = "hm-artifact:"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


class ArtifactStoreError(RuntimeError):
    """Base class for artifact storage failures."""


class ArtifactNotFoundError(ArtifactStoreError):
    """The opaque handle does not identify an artifact."""


class ArtifactAccessDeniedError(ArtifactStoreError):
    """The caller does not own the exact artifact partition."""


class ArtifactExpiredError(ArtifactStoreError):
    """The artifact passed its configured TTL."""


class ArtifactQuotaExceededError(ArtifactStoreError):
    """Writing the artifact would exceed the tenant quota."""


@dataclass(frozen=True)
class StoredToolOutput:
    handle: str
    content_sha256: str
    byte_count: int
    media_type: str
    expires_at: float


class ToolOutputStore:
    """Persist raw bytes independently from model-facing text projection."""

    def __init__(
        self,
        root: Path,
        *,
        quota_bytes: int,
        ttl_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if isinstance(quota_bytes, bool) or quota_bytes <= 0:
            raise ValueError("quota_bytes must be positive")
        if isinstance(ttl_seconds, bool) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.root = Path(root).expanduser().absolute()
        self.quota_bytes = quota_bytes
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._mkdir(self.root)
        self._mkdir(self.root / "index")
        self._mkdir(self.root / "data")
        self._lock_path = self.root / ".lock"
        self._ensure_private_file(self._lock_path)

    def write(
        self,
        *,
        tenant_id: str,
        session_id: str,
        run_id: str,
        content: bytes,
        media_type: str,
    ) -> StoredToolOutput:
        identity = self._identity(tenant_id, session_id, run_id)
        if not isinstance(content, bytes):
            raise TypeError("artifact content must be bytes")
        if not isinstance(media_type, str) or not media_type.strip():
            raise ValueError("artifact media_type must be non-empty")
        now = self._clock()
        with self._locked():
            self._remove_expired(now)
            used = self._tenant_usage(identity["tenant_sha256"])
            if len(content) > self.quota_bytes - used:
                raise ArtifactQuotaExceededError("tenant tool-output quota exceeded")

            token = self._new_token()
            partition = (
                self.root
                / "data"
                / identity["tenant_sha256"]
                / identity["session_sha256"]
                / identity["run_sha256"]
            )
            self._mkdir(partition)
            blob_path = partition / f"{token}.blob"
            metadata_path = self.root / "index" / f"{token}.json"
            content_sha256 = hashlib.sha256(content).hexdigest()
            expires_at = now + self.ttl_seconds
            metadata = {
                "schema_version": 1,
                **identity,
                "blob_ref": blob_path.relative_to(self.root).as_posix(),
                "byte_count": len(content),
                "content_sha256": content_sha256,
                "media_type": media_type,
                "created_at": now,
                "expires_at": expires_at,
            }
            self._atomic_write(blob_path, content)
            try:
                self._atomic_write(
                    metadata_path,
                    json.dumps(
                        metadata,
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("ascii"),
                )
            except BaseException:
                blob_path.unlink(missing_ok=True)
                raise
        return StoredToolOutput(
            handle=f"{_HANDLE_PREFIX}{token}",
            content_sha256=content_sha256,
            byte_count=len(content),
            media_type=media_type,
            expires_at=expires_at,
        )

    def read(
        self,
        handle: str,
        *,
        tenant_id: str,
        session_id: str,
        run_id: str,
    ) -> bytes:
        token = self._parse_handle(handle)
        expected = self._identity(tenant_id, session_id, run_id)
        with self._locked():
            metadata_path = self.root / "index" / f"{token}.json"
            metadata = self._read_metadata(metadata_path)
            if self._clock() >= metadata["expires_at"]:
                self._delete_artifact(metadata_path, metadata)
                raise ArtifactExpiredError("artifact has expired")
            if any(metadata.get(key) != value for key, value in expected.items()):
                raise ArtifactAccessDeniedError("artifact partition access denied")
            blob_path = self._blob_path(metadata)
            try:
                content = blob_path.read_bytes()
            except FileNotFoundError as exc:
                raise ArtifactNotFoundError("artifact bytes are missing") from exc
            if len(content) != metadata.get("byte_count"):
                raise ArtifactStoreError("artifact byte count mismatch")
            if hashlib.sha256(content).hexdigest() != metadata.get("content_sha256"):
                raise ArtifactStoreError("artifact content hash mismatch")
            return content

    def cleanup_expired(self) -> int:
        with self._locked():
            return self._remove_expired(self._clock())

    @staticmethod
    def _identity(tenant_id: str, session_id: str, run_id: str) -> dict[str, str]:
        values = {"tenant": tenant_id, "session": session_id, "run": run_id}
        for label, value in values.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label}_id must be non-empty")
        return {
            f"{label}_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()
            for label, value in values.items()
        }

    def _new_token(self) -> str:
        for _ in range(10):
            token = secrets.token_urlsafe(32)
            if not (self.root / "index" / f"{token}.json").exists():
                return token
        raise ArtifactStoreError("unable to allocate an artifact handle")

    @staticmethod
    def _parse_handle(handle: str) -> str:
        if not isinstance(handle, str) or not handle.startswith(_HANDLE_PREFIX):
            raise ArtifactNotFoundError("invalid artifact handle")
        token = handle.removeprefix(_HANDLE_PREFIX)
        if _TOKEN_RE.fullmatch(token) is None:
            raise ArtifactNotFoundError("invalid artifact handle")
        return token

    def _tenant_usage(self, tenant_sha256: str) -> int:
        total = 0
        for metadata_path in (self.root / "index").glob("*.json"):
            metadata = self._read_metadata(metadata_path)
            if metadata.get("tenant_sha256") == tenant_sha256:
                total += int(metadata["byte_count"])
        return total

    def _remove_expired(self, now: float) -> int:
        removed = 0
        for metadata_path in (self.root / "index").glob("*.json"):
            metadata = self._read_metadata(metadata_path)
            if now >= float(metadata["expires_at"]):
                self._delete_artifact(metadata_path, metadata)
                removed += 1
        return removed

    def _delete_artifact(self, metadata_path: Path, metadata: dict[str, object]) -> None:
        self._blob_path(metadata).unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)

    def _blob_path(self, metadata: dict[str, object]) -> Path:
        ref = metadata.get("blob_ref")
        if not isinstance(ref, str) or not ref or "\\" in ref:
            raise ArtifactStoreError("artifact metadata has an invalid blob reference")
        path = (self.root / ref).resolve(strict=False)
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ArtifactStoreError("artifact blob reference escapes the store") from exc
        if path.is_symlink():
            raise ArtifactStoreError("artifact blob must not be a symlink")
        return path

    @staticmethod
    def _read_metadata(path: Path) -> dict[str, object]:
        if path.is_symlink():
            raise ArtifactStoreError("artifact metadata must not be a symlink")
        try:
            payload = json.loads(path.read_text(encoding="ascii"))
        except FileNotFoundError as exc:
            raise ArtifactNotFoundError("artifact handle was not found") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactStoreError("artifact metadata is unreadable") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ArtifactStoreError("artifact metadata schema is invalid")
        return payload

    def _atomic_write(self, path: Path, content: bytes) -> None:
        self._mkdir(path.parent)
        temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _mkdir(path: Path) -> None:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path, 0o700)

    @staticmethod
    def _ensure_private_file(path: Path) -> None:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(descriptor)
        os.chmod(path, 0o600)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._lock_path.open("r+b") as handle:
            self._lock(handle)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _lock(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


__all__ = [
    "ArtifactAccessDeniedError",
    "ArtifactExpiredError",
    "ArtifactNotFoundError",
    "ArtifactQuotaExceededError",
    "ArtifactStoreError",
    "StoredToolOutput",
    "ToolOutputStore",
]
