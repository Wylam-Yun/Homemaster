"""MCP-specific configuration tests."""

from __future__ import annotations

import pytest

from homemaster.config import HomeMasterConfig
from homemaster.mcp.types import McpHttpServerConfig, McpStdioServerConfig, McpWebSocketServerConfig


def test_mcp_config_parses_stdio_http_and_explicit_unsupported_websocket() -> None:
    config = HomeMasterConfig.model_validate(
        {
            "mcp": {
                "servers": {
                    "local": {
                        "transport": "stdio",
                        "command": "python",
                        "args": ["server.py"],
                        "env": {"TOKEN": "secret", "DATABASE_URL": "opaque-value"},
                    },
                    "remote": {
                        "transport": "http",
                        "url": "https://user:pass@example.test/mcp",
                        "headers": {"Authorization": "Bearer secret"},
                    },
                    "future": {"transport": "websocket", "url": "wss://example.test/mcp"},
                }
            }
        }
    )

    assert isinstance(config.mcp.servers["local"], McpStdioServerConfig)
    assert isinstance(config.mcp.servers["remote"], McpHttpServerConfig)
    assert isinstance(config.mcp.servers["future"], McpWebSocketServerConfig)
    summary = config.mcp.public_summary()
    assert summary["servers"]["local"]["config"]["env"] == {
        "TOKEN": "secret",
        "DATABASE_URL": "opaque-value",
    }
    assert summary["servers"]["remote"]["config"]["url"] == (
        "https://user:pass@example.test/mcp"
    )
    assert summary["servers"]["future"]["status"] == "unsupported"


def test_mcp_config_rejects_non_http_streamable_url() -> None:
    with pytest.raises(ValueError):
        McpHttpServerConfig(transport="http", url="file:///tmp/socket")
