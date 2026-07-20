"""Application-level canonical tool catalog and immutable per-run views."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from homemaster.tools.contracts import RegisteredTool, ToolDefinition, ToolProvenance


class ToolCatalogError(ValueError):
    """Raised when catalog registration or view freezing violates a contract."""


class ToolLookupStatus(StrEnum):
    ENABLED = "enabled"
    UNKNOWN_TOOL = "unknown_tool"
    TOOL_DISABLED = "tool_disabled"


@dataclass(frozen=True)
class CatalogOverrideAuthorization:
    internal_id: str
    existing_snapshot_sha256: str
    replacement_snapshot_sha256: str
    existing_provenance: ToolProvenance
    replacement_provenance: ToolProvenance
    authorized_by: str
    reason: str

    def __post_init__(self) -> None:
        if not self.internal_id:
            raise ValueError("override internal_id is required")
        for label, digest in (
            ("existing", self.existing_snapshot_sha256),
            ("replacement", self.replacement_snapshot_sha256),
        ):
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError(f"override {label} snapshot must be a SHA-256 digest")
        for label, value in (("authorized_by", self.authorized_by), ("reason", self.reason)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"override {label} is required")


@dataclass(frozen=True)
class ToolLookupResult:
    status: ToolLookupStatus
    tool: RegisteredTool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ToolLookupStatus):
            raise TypeError("lookup status must be ToolLookupStatus")
        if self.status is ToolLookupStatus.ENABLED:
            if not isinstance(self.tool, RegisteredTool):
                raise ValueError("enabled lookup requires a registered tool")
        elif self.tool is not None:
            raise ValueError("disabled or unknown lookup cannot expose a tool")


class ToolCatalog:
    """Store canonical registered tools by stable id in insertion order."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        tool: RegisteredTool,
        *,
        override: CatalogOverrideAuthorization | None = None,
    ) -> None:
        if not isinstance(tool, RegisteredTool):
            raise TypeError("catalog entries must be RegisteredTool values")
        internal_id = tool.definition.internal_id
        existing = self._tools.get(internal_id)
        if existing is None:
            if override is not None:
                raise ToolCatalogError("override authorization cannot create a new tool")
            self._tools[internal_id] = tool
            return
        if override is None:
            raise ToolCatalogError(
                _collision_message("duplicate internal id", existing.definition, tool.definition)
            )
        self._validate_override(existing=existing, replacement=tool, authorization=override)
        self._tools[internal_id] = tool

    def get(self, internal_id: str) -> RegisteredTool | None:
        return self._tools.get(internal_id)

    def list_tools(self) -> tuple[RegisteredTool, ...]:
        return tuple(self._tools.values())

    def definitions(self, *, source: str | None = None) -> tuple[ToolDefinition, ...]:
        return tuple(
            tool.definition
            for tool in self._tools.values()
            if source is None or tool.definition.provenance.source == source
        )

    def freeze(self, enabled_tool_ids: list[str] | tuple[str, ...]) -> ToolView:
        if isinstance(enabled_tool_ids, str):
            raise TypeError("enabled_tool_ids must be an ordered sequence")
        enabled_ids = tuple(enabled_tool_ids)
        if len(enabled_ids) != len(set(enabled_ids)):
            raise ToolCatalogError("enabled tool ids must be unique")
        missing = [internal_id for internal_id in enabled_ids if internal_id not in self._tools]
        if missing:
            raise ToolCatalogError(f"unknown enabled tool ids: {missing}")
        selected = tuple(self._tools[internal_id] for internal_id in enabled_ids)
        return ToolView(
            selected,
            catalog_internal_ids=frozenset(self._tools),
            catalog_aliases=frozenset(
                tool.definition.model_alias for tool in self._tools.values()
            ),
        )

    @staticmethod
    def _validate_override(
        *,
        existing: RegisteredTool,
        replacement: RegisteredTool,
        authorization: CatalogOverrideAuthorization,
    ) -> None:
        old = existing.definition
        new = replacement.definition
        if authorization.internal_id != old.internal_id or new.internal_id != old.internal_id:
            raise ToolCatalogError("override authorization internal id mismatch")
        if authorization.existing_snapshot_sha256 != old.snapshot_sha256:
            raise ToolCatalogError("override authorization has a stale existing snapshot")
        if authorization.replacement_snapshot_sha256 != new.snapshot_sha256:
            raise ToolCatalogError("override authorization replacement snapshot mismatch")
        if authorization.existing_provenance != old.provenance:
            raise ToolCatalogError("override authorization existing provenance mismatch")
        if authorization.replacement_provenance != new.provenance:
            raise ToolCatalogError("override authorization replacement provenance mismatch")


class ToolView:
    """Immutable ordered tool capability snapshot for one run."""

    def __init__(
        self,
        tools: tuple[RegisteredTool, ...],
        *,
        catalog_internal_ids: frozenset[str],
        catalog_aliases: frozenset[str],
    ) -> None:
        by_id: dict[str, RegisteredTool] = {}
        by_alias: dict[str, RegisteredTool] = {}
        for tool in tools:
            definition = tool.definition
            if definition.model_alias in by_alias:
                other = by_alias[definition.model_alias]
                raise ToolCatalogError(
                    _collision_message(
                        f"model alias conflict: {definition.model_alias}",
                        other.definition,
                        definition,
                    )
                )
            by_id[definition.internal_id] = tool
            by_alias[definition.model_alias] = tool
        self._tools = tools
        self._by_id = MappingProxyType(by_id)
        self._by_alias = MappingProxyType(by_alias)
        self._catalog_internal_ids = catalog_internal_ids
        self._catalog_aliases = catalog_aliases
        self._view_id = _view_id(tools)

    @property
    def view_id(self) -> str:
        return self._view_id

    @property
    def enabled_tool_ids(self) -> tuple[str, ...]:
        return tuple(tool.definition.internal_id for tool in self._tools)

    def is_enabled(self, internal_id: str) -> bool:
        return internal_id in self._by_id

    def manifests(self) -> tuple[dict[str, object], ...]:
        return tuple(tool.definition.to_model_manifest() for tool in self._tools)

    def list_tools(self) -> tuple[RegisteredTool, ...]:
        return self._tools

    def lookup(self, identifier: str) -> ToolLookupResult:
        tool = self._by_id.get(identifier) or self._by_alias.get(identifier)
        if tool is not None:
            return ToolLookupResult(status=ToolLookupStatus.ENABLED, tool=tool)
        if identifier in self._catalog_internal_ids or identifier in self._catalog_aliases:
            return ToolLookupResult(status=ToolLookupStatus.TOOL_DISABLED)
        return ToolLookupResult(status=ToolLookupStatus.UNKNOWN_TOOL)


def _view_id(tools: tuple[RegisteredTool, ...]) -> str:
    payload = [
        {
            "internal_id": tool.definition.internal_id,
            "snapshot_sha256": tool.definition.snapshot_sha256,
        }
        for tool in tools
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _collision_message(
    prefix: str,
    first: ToolDefinition,
    second: ToolDefinition,
) -> str:
    return (
        f"{prefix}; first={first.internal_id} "
        f"({first.provenance.source}:{first.provenance.reference}); "
        f"second={second.internal_id} "
        f"({second.provenance.source}:{second.provenance.reference})"
    )
