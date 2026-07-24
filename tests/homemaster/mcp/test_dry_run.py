from __future__ import annotations

import json

import pytest

from homemaster.cli.dry_run import _probe_mcp, build_dry_run_preview
from homemaster.cli.renderers import OutputFormat, render_dry_run
from homemaster.config import HomeMasterConfig
from homemaster.mcp.client import McpConnection


def _config() -> HomeMasterConfig:
    return HomeMasterConfig.model_validate(
        {
            "mcp": {
                "servers": {
                    "remote": {
                        "transport": "http",
                        "url": "https://user:password@example.test/mcp",
                        "headers": {"X-Credential": "opaque-secret"},
                    }
                }
            }
        }
    )


def test_dry_run_reports_static_mcp_config_without_connecting(monkeypatch) -> None:
    async def forbidden(_config):
        raise AssertionError("ordinary dry-run probed MCP")

    monkeypatch.setattr("homemaster.cli.dry_run._probe_mcp", forbidden)
    preview = build_dry_run_preview(prompt="inspect", config=_config())

    assert preview["mcp_discovery"] == "unknown_until_connect"
    assert preview["external_io"] is False
    assert preview["mcp_statuses"] == []
    assert "https://user:password@example.test/mcp" in str(preview)
    assert "opaque-secret" in str(preview)


def test_explicit_probe_records_external_io_and_status(monkeypatch) -> None:
    async def probe(_config):
        return [
            {
                "name": "remote",
                "state": "connected",
                "transport": "http",
                "auth_configured": True,
                "error_code": "",
                "detail": "",
                "tool_count": 1,
                "resource_count": 0,
            }
        ]

    monkeypatch.setattr("homemaster.cli.dry_run._probe_mcp", probe)
    preview = build_dry_run_preview(prompt=None, config=_config(), probe=True)

    assert preview["mcp_discovery"] == "probed"
    assert preview["external_io"] is True
    assert preview["mcp_statuses"][0]["tool_count"] == 1
    assert "external_io: true" in render_dry_run(preview, OutputFormat.TEXT)


@pytest.mark.asyncio
async def test_explicit_probe_writes_private_jsonl_audit(tmp_path) -> None:
    class Session:
        async def initialize(self):
            return None

        async def list_tools(self):
            return []

        async def list_resources(self):
            return []

    async def connector(name, config):
        del name, config
        return McpConnection(Session(), lambda: None)

    config = HomeMasterConfig.model_validate(
        {
            "mcp": {"servers": {"fixture": {"transport": "stdio", "command": "fixture"}}},
            "observability": {"trace_dir": str(tmp_path / "trace")},
        }
    )

    statuses = await _probe_mcp(config, connector=connector)

    assert statuses[0]["state"] == "connected"
    audit_path = tmp_path / "trace" / "mcp_probe_audit.jsonl"
    events = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert [event["type"] for event in events] == [
        "mcp.connect.started",
        "mcp.connect.completed",
        "mcp.close.completed",
    ]
    assert audit_path.stat().st_mode & 0o777 == 0o600
