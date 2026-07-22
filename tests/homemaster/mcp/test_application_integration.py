from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from homemaster.application import RunRequest
from homemaster.cli.composition import create_home_application
from homemaster.config import HomeMasterConfig
from homemaster.mcp.client import McpConnection
from homemaster.tools.catalog import ToolCatalogError, ToolLookupStatus


@dataclass
class FakeSession:
    tools: list[dict[str, object]]

    async def initialize(self) -> None:
        return None

    async def list_tools(self):
        return self.tools

    async def list_resources(self):
        return [{"name": "Readme", "uri": "demo://readme"}]

    async def call_tool(self, name, arguments):
        return {"content": [{"type": "text", "text": f"{name}:{arguments}"}]}

    async def read_resource(self, uri):
        return {"contents": [{"uri": uri, "text": "body"}]}


def _config(tmp_path: Path, *, explicit_skill: bool = False) -> HomeMasterConfig:
    explicit_dirs: list[str] = []
    if explicit_skill:
        skill_root = tmp_path / "skills"
        skill = skill_root / "mcp-query"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\n"
            "name: mcp-query\n"
            "description: Query the demo MCP server.\n"
            "tool_names: [mcp__demo__nested_query]\n"
            "---\n\n"
            "# MCP Query\n",
            encoding="utf-8",
        )
        explicit_dirs.append(str(skill_root))
    return HomeMasterConfig.model_validate(
        {
            "runtime": {"runtime_root": str(tmp_path / "runs")},
            "observability": {"session_dir": str(tmp_path / "sessions")},
            "skills": {
                "user_dirs": [],
                "project_dirs": [],
                "explicit_dirs": explicit_dirs,
            },
            "mcp": {
                "servers": {
                    "demo": {
                        "transport": "stdio",
                        "command": "fixture",
                        "env": {"TOKEN": "server-secret"},
                    },
                    "bad": {
                        "transport": "stdio",
                        "command": "fixture",
                        "env": {"TOKEN": "bad-secret"},
                    },
                },
                "artifact_root": str(tmp_path / "tool-output"),
            },
        }
    )


@pytest.mark.asyncio
async def test_start_connects_once_refreezes_home_and_revalidates_skills(tmp_path) -> None:
    calls: list[str] = []
    closed: list[str] = []

    async def connector(name, config):
        calls.append(name)
        if name == "bad":
            raise RuntimeError(f"rejected {config.env['TOKEN']}")

        async def close() -> None:
            closed.append(name)

        return McpConnection(
            FakeSession(
                [
                    {
                        "name": "nested-query",
                        "description": "Nested query",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"filters": {"type": "object"}},
                        },
                    }
                ]
            ),
            close,
        )

    bundle = create_home_application(
        config=_config(tmp_path, explicit_skill=True),
        run_label="mcp-start",
        mcp_connector=connector,
    )
    assert bundle.application.started is False
    assert bundle.skill_registry.get("mcp-query") is None

    await asyncio.gather(bundle.application.start(), bundle.application.start())

    assert bundle.application.started is True
    assert calls == ["demo", "bad"]
    assert "mcp__demo__nested_query" in bundle.application.profiles["home"].model_tool_names
    assert bundle.skill_registry.get("mcp-query") is not None
    statuses = {status.name: status for status in bundle.mcp_manager.list_statuses()}
    assert statuses["demo"].state == "connected"
    assert statuses["bad"].state == "failed"
    assert "bad-secret" not in statuses["bad"].detail

    builtin_only = bundle.application._view(
        RunRequest(text="query", enabled_tool_ids=("home.observe.v1",)),
        bundle.application.profiles["home"],
    )
    assert builtin_only.lookup("mcp__demo__nested_query").status is ToolLookupStatus.TOOL_DISABLED

    await bundle.application.aclose()
    assert closed == ["demo"]
    assert "server-secret" not in bundle.mcp_audit_path.read_text(encoding="utf-8")
    assert "bad-secret" not in bundle.mcp_audit_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_alias_conflict_rolls_back_connected_manager_without_catalog_mutation(
    tmp_path,
) -> None:
    closed = 0

    async def connector(name, config):
        del name, config

        async def close() -> None:
            nonlocal closed
            closed += 1

        return McpConnection(
            FakeSession(
                [
                    {"name": "same-name", "inputSchema": {"type": "object"}},
                    {"name": "same_name", "inputSchema": {"type": "object"}},
                ]
            ),
            close,
        )

    payload = _config(tmp_path).model_dump(mode="python")
    payload["mcp"]["servers"] = {"demo": payload["mcp"]["servers"]["demo"]}
    config = HomeMasterConfig.model_validate(payload)
    bundle = create_home_application(config=config, mcp_connector=connector)
    before = bundle.application.catalog.list_tools()

    with pytest.raises(ToolCatalogError, match="alias conflict"):
        await bundle.application.start()

    assert bundle.application.catalog.list_tools() == before
    assert bundle.application.resource_scope.closed is True
    assert closed == 1
