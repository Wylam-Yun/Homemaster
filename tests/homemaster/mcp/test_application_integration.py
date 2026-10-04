from __future__ import annotations

import asyncio
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

import homemaster.application.composition.base as composition
from homemaster.application.composition import compose_application
from homemaster.config import HomeMasterConfig
from homemaster.mcp.client import McpConnection
from homemaster.tools.base import ToolRegistryError


@pytest.fixture(autouse=True)
def _isolate_memory_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    """application.start() also boots memory services; stub the managed
    Neo4j process and MindMemOS backends so these tests exercise MCP wiring
    without a live installation."""

    class _FakeManagedNeo4jRuntime:
        def __init__(self, _memory_config: object) -> None:
            pass

        async def start(self) -> None:
            pass

        async def close(self) -> None:
            pass

    class _FakeEmbeddedMindMemOS:
        available = True
        unavailable_cause = None

        def __init__(self, _config: object) -> None:
            pass

        def add_schema_episode(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def start(self) -> None:
            pass

        async def close(self) -> None:
            pass

    @dataclass
    class _MemoryRequestContext:
        request_id: str
        account_id: str
        project_id: str
        api_key_uuid: str
        user_id: str
        app_id: str
        session_id: str | None
        agent_id: str

    mindmemos_pkg = types.ModuleType("mindmemos")
    mindmemos_typing = types.ModuleType("mindmemos.typing")
    mindmemos_typing.MemoryRequestContext = _MemoryRequestContext  # type: ignore[attr-defined]
    mindmemos_pkg.typing = mindmemos_typing  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mindmemos", mindmemos_pkg)
    monkeypatch.setitem(sys.modules, "mindmemos.typing", mindmemos_typing)
    monkeypatch.setattr(composition, "ManagedNeo4jRuntime", _FakeManagedNeo4jRuntime)
    monkeypatch.setattr(composition, "EmbeddedMindMemOS", _FakeEmbeddedMindMemOS)


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
            "memory": {"data_root": str(tmp_path / "memory")},
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
async def test_start_connects_once_refreezes_home_without_gating_skills(tmp_path) -> None:
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

    bundle = compose_application(
        config=_config(tmp_path, explicit_skill=True),
        run_label="mcp-start",
        mcp_connector=connector,
    )
    assert bundle.application.started is False
    skill_before_mcp = bundle.skill_registry.get("mcp-query")
    assert skill_before_mcp is not None

    await asyncio.gather(bundle.application.start(), bundle.application.start())

    assert bundle.application.started is True
    assert calls == ["demo", "bad"]
    names = bundle.application.registry.all_names()
    assert "mcp__demo__nested_query" in names
    assert "list_mcp_resources" in names
    assert "read_mcp_resource" in names
    assert bundle.skill_registry.get("mcp-query") is skill_before_mcp
    statuses = {status.name: status for status in bundle.mcp_manager.list_statuses()}
    assert statuses["demo"].state == "connected"
    assert statuses["bad"].state == "failed"
    assert "bad-secret" in statuses["bad"].detail
    assert "mcp__demo__nested_query" in {
        schema["name"] for schema in bundle.application.registry.to_api_schema()
    }

    await bundle.application.aclose()
    assert closed == ["demo"]
    audit = bundle.mcp_audit_path.read_text(encoding="utf-8")
    assert "bad-secret" in audit


@pytest.mark.asyncio
async def test_alias_conflict_rolls_back_connected_manager_without_registry_mutation(
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
    # Re-validating a dumped managed_local block fails the explicit-mode
    # credential check; the test uses the all-empty default anyway.
    del payload["memory"]["neo4j"]
    config = HomeMasterConfig.model_validate(payload)
    bundle = compose_application(config=config, mcp_connector=connector)
    before = bundle.application.registry.list_tools()

    with pytest.raises(ToolRegistryError, match="duplicate tool name"):
        await bundle.application.start()

    assert bundle.application.registry.list_tools() == before
    assert bundle.application.resource_scope.closed is True
    assert closed == 1
