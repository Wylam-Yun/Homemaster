"""Optional MCP client integration."""

from homemaster.mcp.audit import McpAuditLog
from homemaster.mcp.types import (
    McpConnectionStatus,
    McpHttpServerConfig,
    McpPayload,
    McpResourceInfo,
    McpStdioServerConfig,
    McpToolInfo,
    McpWebSocketServerConfig,
)

__all__ = [
    "McpAuditLog",
    "McpConnectionStatus",
    "McpHttpServerConfig",
    "McpPayload",
    "McpResourceInfo",
    "McpStdioServerConfig",
    "McpToolInfo",
    "McpWebSocketServerConfig",
]
