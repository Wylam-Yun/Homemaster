"""Canonical ToolCatalog adapters for discovered MCP tools and resources."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from homemaster.artifacts.tool_output_store import ArtifactStoreError, ToolOutputStore
from homemaster.config.config import redact_config_value
from homemaster.mcp.client import McpCallError, McpClientError
from homemaster.mcp.types import McpPayload, McpResourceInfo, McpToolInfo
from homemaster.tools.catalog import ToolCatalog, ToolCatalogError
from homemaster.tools.contracts import (
    ExecutionBackend,
    OutcomeCertainty,
    RegisteredTool,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
)

_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "preview": {"type": "string"},
        "artifact_handle": {"type": "string", "pattern": "^hm-artifact:"},
        "content_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "byte_count": {"type": "integer", "minimum": 0},
        "media_type": {"type": "string"},
        "truncated": {"type": "boolean"},
    },
    "required": [
        "preview",
        "artifact_handle",
        "content_sha256",
        "byte_count",
        "media_type",
        "truncated",
    ],
    "additionalProperties": False,
}


class _McpToolExecutor:
    def __init__(self, manager: Any, info: McpToolInfo, store: ToolOutputStore, preview: int):
        self._manager = manager
        self._info = info
        self._store = store
        self._preview = preview

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            payload = await self._manager.call_tool(
                self._info.server_name,
                self._info.name,
                dict(arguments),
            )
        except McpCallError as exc:
            return _outcome_unknown(exc)
        except McpClientError as exc:
            return _failure(exc, backend_attempted=False)
        return _persisted_result(
            payload,
            context=context,
            store=self._store,
            preview_chars=self._preview,
            secrets=getattr(self._manager, "secret_values", ()),
        )


class _McpReadResourceExecutor:
    def __init__(
        self,
        manager: Any,
        store: ToolOutputStore,
        resources: Mapping[str, McpResourceInfo],
        preview: int,
    ):
        self._manager = manager
        self._store = store
        self._resources = dict(resources)
        self._preview = preview

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            resource = self._resources[str(arguments["resource_id"])]
            payload = await self._manager.read_resource(
                resource.server_name,
                resource.uri,
            )
        except (KeyError, McpClientError) as exc:
            return _failure(exc, backend_attempted=not isinstance(exc, KeyError))
        return _persisted_result(
            payload,
            context=context,
            store=self._store,
            preview_chars=self._preview,
            secrets=(*getattr(self._manager, "secret_values", ()), resource.uri),
            hide_resource_uris=True,
        )


class _McpListResourcesExecutor:
    def __init__(
        self,
        manager: Any,
        store: ToolOutputStore,
        resources: Mapping[str, McpResourceInfo],
        preview: int,
    ):
        self._manager = manager
        self._store = store
        self._resources = dict(resources)
        self._preview = preview

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        del arguments
        resources = [
            {
                "resource_id": resource_id,
                "server": item.server_name,
                "name": item.name,
                "description": item.description,
            }
            for resource_id, item in self._resources.items()
        ]
        return _persisted_result(
            McpPayload(payload={"resources": resources}),
            context=context,
            store=self._store,
            preview_chars=self._preview,
            secrets=getattr(self._manager, "secret_values", ()),
        )


def build_mcp_registered_tools(
    manager: Any,
    store: ToolOutputStore,
    *,
    preview_chars: int = 4000,
) -> tuple[RegisteredTool, ...]:
    tools: list[RegisteredTool] = []
    for info in manager.list_tools():
        server = _segment(info.server_name)
        name = _segment(info.name)
        alias = _alias(info.server_name, info.name)
        definition = ToolDefinition(
            internal_id=f"mcp.{server}.{name}.v1",
            model_alias=alias,
            description=info.description or f"MCP tool {info.name}",
            input_schema=info.input_schema,
            output_schema=_OUTPUT_SCHEMA,
            verification_policy=VerificationPolicy(),
            provenance=ToolProvenance(
                source="mcp",
                reference=f"{info.server_name}:{info.name}",
            ),
            version="1.9.0",
            execution_backend=ExecutionBackend.MCP,
            # SDK mutation annotations are UNVERIFIED in the live deployment.
            # Fail closed until a tool has an independently verified read-only contract.
            state_effects=("mcp.remote_state",),
        )
        tools.append(
            RegisteredTool(
                definition=definition,
                executor=_McpToolExecutor(manager, info, store, preview_chars),
            )
        )

    resources = tuple(manager.list_resources())
    tools.extend(_resource_tools(manager, store, resources, preview_chars))
    return tuple(tools)


def _resource_tools(
    manager: Any,
    store: ToolOutputStore,
    resources: tuple[McpResourceInfo, ...],
    preview_chars: int,
) -> tuple[RegisteredTool, RegisteredTool]:
    by_id = {_resource_id(item): item for item in resources}
    if len(by_id) != len(resources):
        raise ToolCatalogError("duplicate MCP resource identity")
    list_definition = ToolDefinition(
        internal_id="mcp.list_resources.v1",
        model_alias="list_mcp_resources",
        description="List MCP resources available from connected servers.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema=_OUTPUT_SCHEMA,
        verification_policy=VerificationPolicy(),
        provenance=ToolProvenance(source="mcp", reference="connected-resources"),
        version="1.9.0",
        execution_backend=ExecutionBackend.MCP,
    )
    read_definition = ToolDefinition(
        internal_id="mcp.read_resource.v1",
        model_alias="read_mcp_resource",
        description="Read an MCP resource by its HomeMaster opaque resource id.",
        input_schema={
            "type": "object",
            "properties": {
                "resource_id": {"type": "string", "enum": sorted(by_id)},
            },
            "required": ["resource_id"],
            "additionalProperties": False,
        },
        output_schema=_OUTPUT_SCHEMA,
        verification_policy=VerificationPolicy(),
        provenance=ToolProvenance(source="mcp", reference="connected-resources"),
        version="1.9.0",
        execution_backend=ExecutionBackend.MCP,
    )
    return (
        RegisteredTool(
            definition=list_definition,
            executor=_McpListResourcesExecutor(manager, store, by_id, preview_chars),
        ),
        RegisteredTool(
            definition=read_definition,
            executor=_McpReadResourceExecutor(manager, store, by_id, preview_chars),
        ),
    )


def register_mcp_tools_atomically(
    catalog: ToolCatalog,
    tools: Sequence[RegisteredTool],
) -> tuple[str, ...]:
    existing_ids = {tool.definition.internal_id for tool in catalog.list_tools()}
    existing_aliases = {tool.definition.model_alias for tool in catalog.list_tools()}
    staged_ids: set[str] = set()
    staged_aliases: set[str] = set()
    for tool in tools:
        definition = tool.definition
        if definition.internal_id in existing_ids or definition.internal_id in staged_ids:
            raise ToolCatalogError(f"MCP internal id conflict: {definition.internal_id}")
        if definition.model_alias in existing_aliases or definition.model_alias in staged_aliases:
            raise ToolCatalogError(f"MCP model alias conflict: {definition.model_alias}")
        staged_ids.add(definition.internal_id)
        staged_aliases.add(definition.model_alias)
    for tool in tools:
        catalog.register(tool)
    return tuple(tool.definition.internal_id for tool in tools)


def _persisted_result(
    payload: McpPayload,
    *,
    context: ToolExecutionContext,
    store: ToolOutputStore,
    preview_chars: int,
    secrets: Sequence[str],
    hide_resource_uris: bool = False,
) -> ToolExecutionResult:
    try:
        raw = json.dumps(
            payload.payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        return _failure(
            ValueError(f"MCP payload is not canonical JSON: {type(exc).__name__}"),
            backend_attempted=True,
        )
    try:
        stored = store.write(
            tenant_id=context.permission_subject.tenant_id,
            session_id=context.session_id,
            run_id=context.run_id,
            content=raw,
            media_type=payload.media_type,
        )
    except ArtifactStoreError as exc:
        return _failure(exc, backend_attempted=True)

    redacted = redact_config_value(payload.payload)
    if hide_resource_uris:
        redacted = _redact_resource_uris(redacted)
    preview = json.dumps(
        redacted,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    for secret in secrets:
        if secret:
            preview = preview.replace(secret, "[REDACTED]")
    truncated = len(preview) > preview_chars
    data = {
        "preview": preview[:preview_chars],
        "artifact_handle": stored.handle,
        "content_sha256": stored.content_sha256,
        "byte_count": stored.byte_count,
        "media_type": stored.media_type,
        "truncated": truncated,
    }
    if payload.is_error:
        return ToolExecutionResult(
            status=ToolExecutionStatus.FAILURE,
            text=data["preview"],
            data=data,
            error=ToolExecutionError(
                code="mcp_tool_error",
                message="MCP server returned a tool error",
                details={"artifact_handle": stored.handle},
            ),
            backend_attempted=True,
        )
    return ToolExecutionResult(
        status=ToolExecutionStatus.SUCCESS,
        text=data["preview"],
        data=data,
        backend_attempted=True,
    )


def _redact_resource_uris(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: (
                "[REDACTED]"
                if isinstance(key, str) and key.casefold() == "uri"
                else _redact_resource_uris(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_resource_uris(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_resource_uris(item) for item in value)
    return value


def _failure(exc: BaseException, *, backend_attempted: bool) -> ToolExecutionResult:
    return ToolExecutionResult(
        status=ToolExecutionStatus.FAILURE if backend_attempted else ToolExecutionStatus.INVALID,
        error=ToolExecutionError(
            code="mcp_execution_failed" if backend_attempted else "mcp_arguments_invalid",
            message=str(exc) or type(exc).__name__,
        ),
        backend_attempted=backend_attempted,
    )


def _outcome_unknown(exc: BaseException) -> ToolExecutionResult:
    return ToolExecutionResult(
        status=ToolExecutionStatus.OUTCOME_UNKNOWN,
        error=ToolExecutionError(
            code="mcp_outcome_unknown",
            message=str(exc) or type(exc).__name__,
        ),
        outcome_certainty=OutcomeCertainty.UNKNOWN,
        backend_attempted=True,
    )


def _resource_id(info: McpResourceInfo) -> str:
    digest = hashlib.sha256(f"{info.server_name}\0{info.uri}".encode()).hexdigest()
    return f"mcp-resource:{digest[:24]}"


def _segment(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "_", value.casefold()).strip("_-") or "item"
    if not normalized[0].isalpha():
        normalized = f"item_{normalized}"
    if len(normalized) > 40:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
        normalized = f"{normalized[:31]}_{digest}"
    return normalized


def _alias(server: str, tool: str) -> str:
    value = f"mcp__{_alias_segment(server)}__{_alias_segment(tool)}"
    if len(value) <= 64:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{value[:55]}_{digest}"


def _alias_segment(value: str) -> str:
    """Return a segment accepted by the model-facing alias contract."""

    return _segment(value).replace("-", "_")


__all__ = ["build_mcp_registered_tools", "register_mcp_tools_atomically"]
