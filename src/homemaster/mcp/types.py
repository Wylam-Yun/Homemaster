"""Typed MCP configuration and discovery contracts.

Configuration seed adapted from OpenHarness 9b2efd7 ``src/openharness/mcp/types.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class McpStdioServerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    transport: Literal["stdio"] = "stdio"
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    enabled: bool = True

    @field_validator("command")
    @classmethod
    def _command_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("MCP stdio command must not be blank")
        return value


class McpHttpServerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    transport: Literal["http"] = "http"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("MCP streamable HTTP URL must use http or https")
        return value


class McpWebSocketServerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    transport: Literal["websocket"] = "websocket"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


McpServerConfig = Annotated[
    McpStdioServerConfig | McpHttpServerConfig | McpWebSocketServerConfig,
    Field(discriminator="transport"),
]


class McpSettingsConfig(BaseModel):
    servers: dict[str, McpServerConfig] = Field(default_factory=dict)
    connect_timeout_s: float = 15.0
    call_timeout_s: float = 60.0
    artifact_root: str = "~/.homemaster/artifacts/tool-output"
    artifact_quota_bytes: int = 64 * 1024 * 1024
    artifact_ttl_seconds: float = 7 * 24 * 60 * 60
    preview_chars: int = 4000

    @field_validator("connect_timeout_s", "call_timeout_s", "artifact_ttl_seconds")
    @classmethod
    def _positive_float(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("MCP timeout and TTL values must be positive")
        return value

    @field_validator("artifact_quota_bytes", "preview_chars")
    @classmethod
    def _positive_int(cls, value: int) -> int:
        if isinstance(value, bool) or value <= 0:
            raise ValueError("MCP quota and preview values must be positive")
        return value

    def public_summary(self) -> dict[str, Any]:
        from homemaster.config.config import redact_config_value

        servers: dict[str, Any] = {}
        for name, config in sorted(self.servers.items()):
            payload = config.model_dump(mode="json")
            if isinstance(config, McpStdioServerConfig):
                payload["env"] = {key: "[REDACTED]" for key in config.env}
            else:
                payload["headers"] = {key: "[REDACTED]" for key in config.headers}
            servers[name] = {
                "transport": config.transport,
                "enabled": config.enabled,
                "status": "unsupported" if config.transport == "websocket" else "configured",
                "config": redact_config_value(payload),
            }
        return {
            "servers": servers,
            "connect_timeout_s": self.connect_timeout_s,
            "call_timeout_s": self.call_timeout_s,
            "artifact_quota_bytes": self.artifact_quota_bytes,
            "artifact_ttl_seconds": self.artifact_ttl_seconds,
            "preview_chars": self.preview_chars,
        }


@dataclass(frozen=True)
class McpToolInfo:
    server_name: str
    name: str
    description: str
    input_schema: dict[str, object]


@dataclass(frozen=True)
class McpResourceInfo:
    server_name: str
    name: str
    uri: str
    description: str = ""


@dataclass(frozen=True)
class McpConnectionStatus:
    name: str
    state: Literal["connected", "failed", "pending", "disabled"]
    detail: str = ""
    transport: str = "unknown"
    auth_configured: bool = False
    error_code: str = ""
    tools: tuple[McpToolInfo, ...] = ()
    resources: tuple[McpResourceInfo, ...] = ()


@dataclass(frozen=True)
class McpPayload:
    payload: dict[str, object]
    media_type: str = "application/json"
    is_error: bool = False


def mcp_secret_values(configs: dict[str, McpServerConfig]) -> tuple[str, ...]:
    values: list[str] = []
    for config in configs.values():
        source = config.env if isinstance(config, McpStdioServerConfig) else config.headers
        values.extend(value for value in source.values() if value)
        if isinstance(config, (McpHttpServerConfig, McpWebSocketServerConfig)):
            parsed = urlsplit(config.url)
            values.extend(value for value in (parsed.username, parsed.password) if value)
    return tuple(sorted(set(values), key=len, reverse=True))


__all__ = [
    "McpConnectionStatus",
    "McpHttpServerConfig",
    "McpPayload",
    "McpResourceInfo",
    "McpServerConfig",
    "McpSettingsConfig",
    "McpStdioServerConfig",
    "McpToolInfo",
    "McpWebSocketServerConfig",
    "mcp_secret_values",
]
