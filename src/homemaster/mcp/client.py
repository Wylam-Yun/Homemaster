"""Application-owned MCP transport manager.

Protocol flow adapted from OpenHarness 9b2efd7 ``src/openharness/mcp/client.py``;
ownership, typed status, redaction, disconnect fencing, and cleanup are HomeMaster deltas.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

from homemaster.mcp.types import (
    McpConnectionStatus,
    McpHttpServerConfig,
    McpPayload,
    McpResourceInfo,
    McpServerConfig,
    McpStdioServerConfig,
    McpToolInfo,
    McpWebSocketServerConfig,
    mcp_secret_values,
)


class McpClientError(RuntimeError):
    """Base class for MCP connection and call failures."""


class McpFeatureUnavailableError(McpClientError):
    """The optional MCP SDK is not installed."""


class McpServerNotConnectedError(McpClientError):
    """The requested server has no active session."""


class McpCallError(McpClientError):
    """A connected MCP server failed a call."""


class McpCleanupError(McpClientError):
    """One or more MCP connections failed to close."""

    def __init__(self, errors: tuple[BaseException, ...]) -> None:
        self.errors = errors
        super().__init__(
            "MCP cleanup failed: "
            + "; ".join(f"{type(error).__name__}: {error}" for error in errors)
        )


@dataclass(frozen=True)
class McpConnection:
    session: Any
    close: Callable[[], Any]


@dataclass(frozen=True)
class McpAuditFailure:
    event_type: str
    error_type: str
    detail: str


Connector = Callable[[str, McpServerConfig], Awaitable[McpConnection]]
AuditSink = Callable[[dict[str, object]], Any]


class McpClientManager:
    """Manage supported MCP connections with per-server failure isolation."""

    def __init__(
        self,
        server_configs: Mapping[str, McpServerConfig],
        *,
        connector: Connector | None = None,
        connect_timeout_s: float = 15.0,
        call_timeout_s: float = 60.0,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._server_configs = dict(server_configs)
        self._connector = connector or _sdk_connector
        self._connect_timeout_s = connect_timeout_s
        self._call_timeout_s = call_timeout_s
        self._audit_sink = audit_sink
        self._statuses = {
            name: McpConnectionStatus(
                name=name,
                state="disabled" if not config.enabled else "pending",
                transport=config.transport,
                auth_configured=_auth_configured(config),
            )
            for name, config in self._server_configs.items()
        }
        self._connections: dict[str, McpConnection] = {}
        self._audit_failures: list[McpAuditFailure] = []
        self._closed = False
        self.secret_values = mcp_secret_values(self._server_configs)

    async def connect_all(self) -> None:
        if self._closed:
            raise RuntimeError("MCP manager is closed")
        for name, config in self._server_configs.items():
            if not config.enabled:
                continue
            if isinstance(config, McpWebSocketServerConfig):
                self._statuses[name] = McpConnectionStatus(
                    name=name,
                    state="failed",
                    transport=config.transport,
                    auth_configured=_auth_configured(config),
                    error_code="unsupported_transport",
                    detail="unsupported MCP transport: websocket",
                )
                continue
            await self._connect_one(name, config)

    async def _connect_one(self, name: str, config: McpServerConfig) -> None:
        started = time.perf_counter()
        await self._audit("mcp.connect.started", server=name, transport=config.transport)
        connection: McpConnection | None = None
        try:
            async with asyncio.timeout(self._connect_timeout_s):
                connection = await self._connector(name, config)
                session = connection.session
                await session.initialize()
                tool_result = await session.list_tools()
                try:
                    resource_result = await session.list_resources()
                except Exception as exc:
                    if "Method not found" not in str(exc):
                        raise
                    resource_result = []
            tools = tuple(_tool_info(name, item) for item in _items(tool_result, "tools"))
            resources = tuple(
                _resource_info(name, item) for item in _items(resource_result, "resources")
            )
            self._connections[name] = connection
            self._statuses[name] = McpConnectionStatus(
                name=name,
                state="connected",
                transport=config.transport,
                auth_configured=_auth_configured(config),
                tools=tools,
                resources=resources,
            )
            await self._audit(
                "mcp.connect.completed",
                server=name,
                transport=config.transport,
                elapsed_ms=_elapsed_ms(started),
                tool_count=len(tools),
                resource_count=len(resources),
            )
        except asyncio.CancelledError:
            if connection is not None:
                await _best_effort_close(connection)
            await self._audit(
                "mcp.connect.cancelled",
                server=name,
                transport=config.transport,
                elapsed_ms=_elapsed_ms(started),
            )
            raise
        except Exception as exc:
            if connection is not None:
                await _best_effort_close(connection)
            detail = self._redact(str(exc) or type(exc).__name__)
            self._statuses[name] = McpConnectionStatus(
                name=name,
                state="failed",
                detail=detail,
                transport=config.transport,
                auth_configured=_auth_configured(config),
                error_code=_connection_error_code(exc),
            )
            await self._audit(
                "mcp.connect.failed",
                server=name,
                transport=config.transport,
                elapsed_ms=_elapsed_ms(started),
                error_type=type(exc).__name__,
                detail=detail,
            )

    def list_statuses(self) -> tuple[McpConnectionStatus, ...]:
        return tuple(self._statuses[name] for name in sorted(self._statuses))

    def list_tools(self) -> tuple[McpToolInfo, ...]:
        return tuple(tool for status in self.list_statuses() for tool in status.tools)

    def list_resources(self) -> tuple[McpResourceInfo, ...]:
        return tuple(resource for status in self.list_statuses() for resource in status.resources)

    def list_audit_failures(self) -> tuple[McpAuditFailure, ...]:
        return tuple(self._audit_failures)

    def get_server_config(self, name: str) -> McpServerConfig | None:
        return self._server_configs.get(name)

    def update_server_config(self, name: str, config: McpServerConfig) -> None:
        if name not in self._server_configs:
            raise KeyError(f"unknown MCP server: {name}")
        self._server_configs[name] = config
        self.secret_values = mcp_secret_values(self._server_configs)

    async def reconnect_all(self) -> None:
        if self._closed:
            raise RuntimeError("MCP manager is closed")
        connections = tuple(self._connections.items())
        self._connections.clear()
        for name, connection in reversed(connections):
            await _maybe_await(connection.close())
            await self._audit("mcp.close.completed", server=name)
        self._statuses = {
            name: McpConnectionStatus(
                name=name,
                state="disabled" if not config.enabled else "pending",
                transport=config.transport,
                auth_configured=_auth_configured(config),
            )
            for name, config in self._server_configs.items()
        }
        await self.connect_all()

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> McpPayload:
        connection = self._require_connection(server_name)
        started = time.perf_counter()
        await self._audit("mcp.tool.started", server=server_name, tool=tool_name)
        try:
            async with asyncio.timeout(self._call_timeout_s):
                result = await connection.session.call_tool(tool_name, arguments)
        except asyncio.CancelledError:
            await self._audit("mcp.tool.cancelled", server=server_name, tool=tool_name)
            raise
        except Exception as exc:
            if _is_disconnect_error(exc):
                await self._mark_disconnected(server_name, connection, exc)
            detail = self._redact(str(exc) or type(exc).__name__)
            await self._audit(
                "mcp.tool.failed",
                server=server_name,
                tool=tool_name,
                error_type=type(exc).__name__,
                detail=detail,
            )
            raise McpCallError(f"MCP tool call failed: {detail}") from exc
        payload = _model_dump(result)
        await self._audit(
            "mcp.tool.completed",
            server=server_name,
            tool=tool_name,
            elapsed_ms=_elapsed_ms(started),
            is_error=bool(payload.get("isError", payload.get("is_error", False))),
        )
        return McpPayload(
            payload=payload,
            is_error=bool(payload.get("isError", payload.get("is_error", False))),
        )

    async def read_resource(self, server_name: str, uri: str) -> McpPayload:
        connection = self._require_connection(server_name)
        started = time.perf_counter()
        resource_ref = _resource_ref(uri)
        await self._audit("mcp.resource.started", server=server_name, resource_ref=resource_ref)
        try:
            async with asyncio.timeout(self._call_timeout_s):
                result = await connection.session.read_resource(uri)
        except asyncio.CancelledError:
            await self._audit(
                "mcp.resource.cancelled", server=server_name, resource_ref=resource_ref
            )
            raise
        except Exception as exc:
            if _is_disconnect_error(exc):
                await self._mark_disconnected(server_name, connection, exc)
            detail = self._redact(str(exc) or type(exc).__name__)
            await self._audit(
                "mcp.resource.failed",
                server=server_name,
                resource_ref=resource_ref,
                error_type=type(exc).__name__,
                detail=detail,
            )
            raise McpCallError(f"MCP resource read failed: {detail}") from exc
        await self._audit(
            "mcp.resource.completed",
            server=server_name,
            resource_ref=resource_ref,
            elapsed_ms=_elapsed_ms(started),
        )
        return McpPayload(payload=_model_dump(result))

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[BaseException] = []
        for name, connection in reversed(tuple(self._connections.items())):
            try:
                await _maybe_await(connection.close())
                await self._audit("mcp.close.completed", server=name)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                sanitized = RuntimeError(self._redact(str(exc) or type(exc).__name__))
                errors.append(sanitized)
                await self._audit(
                    "mcp.close.failed",
                    server=name,
                    error_type=type(exc).__name__,
                    detail=str(sanitized),
                )
        self._connections.clear()
        if errors:
            raise McpCleanupError(tuple(errors))

    async def close(self) -> None:
        await self.aclose()

    def _require_connection(self, server_name: str) -> McpConnection:
        connection = self._connections.get(server_name)
        if connection is not None:
            return connection
        status = self._statuses.get(server_name)
        detail = status.detail if status is not None else "unknown server"
        raise McpServerNotConnectedError(
            f"MCP server {server_name!r} is not connected: {self._redact(detail)}"
        )

    async def _mark_disconnected(
        self,
        server_name: str,
        connection: McpConnection,
        exc: BaseException,
    ) -> None:
        current = self._connections.get(server_name)
        if current is not connection:
            return
        self._connections.pop(server_name, None)
        await _best_effort_close(connection)
        status = self._statuses[server_name]
        self._statuses[server_name] = replace(
            status,
            state="failed",
            error_code="disconnected",
            detail=self._redact(str(exc) or type(exc).__name__),
            tools=(),
            resources=(),
        )

    def _redact(self, value: str) -> str:
        result = value
        for secret in self.secret_values:
            result = result.replace(secret, "[REDACTED]")
        return result

    async def _audit(self, event_type: str, **payload: object) -> None:
        if self._audit_sink is None:
            return
        safe = {
            key: self._redact(value) if isinstance(value, str) else value
            for key, value in payload.items()
        }
        try:
            await _maybe_await(self._audit_sink({"type": event_type, **safe}))
        except Exception as exc:
            self._audit_failures.append(
                McpAuditFailure(
                    event_type=event_type,
                    error_type=type(exc).__name__,
                    detail=self._redact(str(exc) or type(exc).__name__),
                )
            )


async def _sdk_connector(name: str, config: McpServerConfig) -> McpConnection:
    del name
    try:
        import httpx
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamable_http_client
    except ImportError as exc:
        raise McpFeatureUnavailableError(
            "MCP support requires the optional 'mcp' dependency"
        ) from exc

    stack = AsyncExitStack()
    try:
        if isinstance(config, McpStdioServerConfig):
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=config.command,
                        args=list(config.args),
                        env=config.env or None,
                        cwd=config.cwd,
                    )
                )
            )
        elif isinstance(config, McpHttpServerConfig):
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(headers=config.headers or None)
            )
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(config.url, http_client=http_client)
            )
        else:
            raise McpFeatureUnavailableError("unsupported MCP transport: websocket")
        session = await stack.enter_async_context(
            ClientSession(read_stream, write_stream, read_timeout_seconds=timedelta(seconds=60))
        )
    except BaseException:
        await stack.aclose()
        raise
    return McpConnection(session=session, close=stack.aclose)


def _items(value: Any, attribute: str) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        items = value.get(attribute, [])
    else:
        items = getattr(value, attribute, [])
    return list(items or [])


def _tool_info(server_name: str, value: Any) -> McpToolInfo:
    payload = _model_dump(value)
    schema = payload.get("inputSchema", payload.get("input_schema", {"type": "object"}))
    if not isinstance(schema, dict):
        raise ValueError("MCP tool input schema must be an object")
    return McpToolInfo(
        server_name=server_name,
        name=str(payload.get("name", "")),
        description=str(payload.get("description") or ""),
        input_schema=schema,
    )


def _resource_info(server_name: str, value: Any) -> McpResourceInfo:
    payload = _model_dump(value)
    uri = str(payload.get("uri", ""))
    return McpResourceInfo(
        server_name=server_name,
        name=str(payload.get("name") or uri),
        uri=uri,
        description=str(payload.get("description") or ""),
    )


def _model_dump(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        payload = dump(mode="json", by_alias=True, exclude_none=True)
        if isinstance(payload, dict):
            return payload
    raise ValueError("MCP response must be a structured object")


def _auth_configured(config: McpServerConfig) -> bool:
    if isinstance(config, McpStdioServerConfig):
        return bool(config.env)
    return bool(config.headers)


async def _best_effort_close(connection: McpConnection) -> None:
    try:
        await _maybe_await(connection.close())
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _resource_ref(uri: str) -> str:
    return f"sha256:{hashlib.sha256(uri.encode('utf-8')).hexdigest()}"


def _connection_error_code(exc: BaseException) -> str:
    if isinstance(exc, McpFeatureUnavailableError):
        return "feature_unavailable"
    if isinstance(exc, TimeoutError):
        return "connect_timeout"
    return "connect_failed"


def _is_disconnect_error(exc: BaseException) -> bool:
    if isinstance(exc, (BrokenPipeError, ConnectionError, EOFError)):
        return True
    return type(exc).__name__ in {"BrokenResourceError", "ClosedResourceError", "EndOfStream"}


__all__ = [
    "Connector",
    "McpCallError",
    "McpAuditFailure",
    "McpCleanupError",
    "McpClientError",
    "McpClientManager",
    "McpConnection",
    "McpFeatureUnavailableError",
    "McpServerNotConnectedError",
]
