from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from homemaster.artifacts import ToolOutputStore
from homemaster.channels.feishu_groups import build_feishu_group_tools
from homemaster.mcp.adapter import build_mcp_registered_tools
from homemaster.mcp.types import McpToolInfo
from homemaster.permissions import HomePermissionPolicy, PermissionMode, PermissionSettingsConfig
from homemaster.tools.contracts import (
    ConcurrencyPolicy,
    ExecutionBackend,
    PermissionSubject,
    ToolDefinition,
    ToolProvenance,
    VerificationPolicy,
)


def definition(
    *,
    internal_id: str = "home.observe.v1",
    backend: ExecutionBackend = ExecutionBackend.IN_PROCESS,
    mutating: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        internal_id=internal_id,
        model_alias="action",
        description="Action.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        verification_policy=VerificationPolicy(),
        provenance=ToolProvenance(source="test", reference="permission"),
        version="1.0.0",
        execution_backend=backend,
        concurrency_policy=(
            ConcurrencyPolicy.RESOURCE_KEY if mutating else ConcurrencyPolicy.PARALLEL
        ),
        resource_key="home:backend" if mutating else None,
        state_effects=("device.move",) if mutating else (),
    )


def context(capabilities: tuple[str, ...]):
    return SimpleNamespace(
        run_id="run",
        tool_call_id="call",
        working_directory=Path.cwd(),
        permission_subject=PermissionSubject(
            subject_id="principal",
            channel="gateway",
            tenant_id="tenant",
            capabilities=capabilities,
        ),
    )


@pytest.mark.parametrize(
    ("tool", "capability"),
    [
        (definition(), "device.read"),
        (definition(mutating=True), "device.control"),
        (
            definition(internal_id="mcp.demo.query.v1", backend=ExecutionBackend.MCP),
            "mcp.call",
        ),
    ],
)
def test_typed_capabilities_allow_only_the_matching_tool_family(tool, capability) -> None:
    policy = HomePermissionPolicy(PermissionSettingsConfig())

    denied = policy.evaluate(tool, {}, context(()))
    allowed = policy.evaluate(tool, {}, context((capability,)))

    assert denied.allowed is False
    assert capability in denied.reason
    assert allowed.allowed is True


def test_arguments_roles_and_explicit_allow_cannot_expand_principal_capability() -> None:
    tool = definition(mutating=True)
    policy = HomePermissionPolicy(PermissionSettingsConfig(allowed_tools=(tool.internal_id,)))
    forged = {
        "capabilities": ["device.control"],
        "roles": ["admin"],
        "permission_subject": {"subject_id": "admin"},
    }

    decision = policy.evaluate(tool, forged, context(("tool.read",)))

    assert decision.allowed is False
    assert "device.control" in decision.reason


def test_plan_default_confirmation_and_full_auto_modes_are_distinct() -> None:
    tool = definition(mutating=True)
    subject = context(("device.control",))

    plan = HomePermissionPolicy(PermissionSettingsConfig(mode=PermissionMode.PLAN)).evaluate(
        tool, {}, subject
    )
    default = HomePermissionPolicy(PermissionSettingsConfig(mode=PermissionMode.DEFAULT)).evaluate(
        tool, {}, subject
    )
    auto = HomePermissionPolicy(PermissionSettingsConfig(mode=PermissionMode.FULL_AUTO)).evaluate(
        tool, {}, subject
    )

    assert plan.allowed is False and plan.requires_confirmation is False
    assert default.allowed is False and default.requires_confirmation is True
    assert auto.allowed is True

    confirmed_subject = SimpleNamespace(
        run_id=subject.run_id,
        tool_call_id=subject.tool_call_id,
        permission_subject=replace(
            subject.permission_subject,
            capabilities=("device.control", "tool.auto"),
        ),
    )
    confirmed = HomePermissionPolicy(
        PermissionSettingsConfig(mode=PermissionMode.DEFAULT)
    ).evaluate(tool, {}, confirmed_subject)
    assert confirmed.allowed is True


def test_discovered_mcp_tool_fails_closed_as_mutating_in_plan_and_default(tmp_path) -> None:
    manager = SimpleNamespace(
        list_tools=lambda: (
            McpToolInfo("demo", "query", "Query remote service", {"type": "object"}),
        ),
        list_resources=lambda: (),
    )
    tool = build_mcp_registered_tools(
        manager,
        ToolOutputStore(tmp_path / "artifacts", quota_bytes=4096, ttl_seconds=60),
    )[0].definition
    subject = context(("mcp.call",))

    plan = HomePermissionPolicy(PermissionSettingsConfig(mode=PermissionMode.PLAN)).evaluate(
        tool, {}, subject
    )
    default = HomePermissionPolicy(PermissionSettingsConfig(mode=PermissionMode.DEFAULT)).evaluate(
        tool, {}, subject
    )

    assert plan.allowed is False and plan.requires_confirmation is False
    assert default.allowed is False and default.requires_confirmation is True


def test_sensitive_paths_and_denied_commands_fail_before_explicit_tool_allow(tmp_path) -> None:
    tool = definition()
    policy = HomePermissionPolicy(
        PermissionSettingsConfig(
            allowed_tools=(tool.internal_id,),
            denied_commands=("rm -rf *",),
        )
    )
    subject = context(("tool.read",))

    protected = policy.evaluate(
        tool,
        {"path": str(tmp_path / ".ssh" / "id_ed25519")},
        subject,
    )
    command = policy.evaluate(tool, {"command": "rm -rf workspace"}, subject)

    assert protected.allowed is False
    assert "protected pattern" in protected.reason
    assert command.allowed is False
    assert "command matches deny rule" in command.reason


def test_exact_tool_capability_does_not_grant_other_tools() -> None:
    first = definition(internal_id="home.first.v1")
    second = definition(internal_id="home.second.v1")
    subject = context(("tool:home.first.v1",))
    policy = HomePermissionPolicy(PermissionSettingsConfig())

    assert policy.evaluate(first, {}, subject).allowed is True
    assert policy.evaluate(second, {}, subject).allowed is False


def test_feishu_group_tools_require_exact_capability_before_any_api_call() -> None:
    operations = Mock()
    create, rename = build_feishu_group_tools(operations)
    policy = HomePermissionPolicy(PermissionSettingsConfig(mode=PermissionMode.FULL_AUTO))

    denied = policy.evaluate(
        create.definition,
        {"name": "Operators"},
        context(("tool.mutate", "channel.feishu.group.rename")),
    )
    allowed = policy.evaluate(
        create.definition,
        {"name": "Operators"},
        context(("tool.mutate", "channel.feishu.group.create")),
    )

    assert denied.allowed is False
    assert "channel.feishu.group.create" in denied.reason
    assert allowed.allowed is True
    operations.create.assert_not_called()
    operations.rename.assert_not_called()
    assert rename.definition.required_capabilities == ("channel.feishu.group.rename",)
