from pathlib import Path

from homemaster.permissions import PermissionChecker, PermissionMode, PermissionSettingsConfig
from homemaster.tools import ToolExecutionContext
from homemaster.tools.contracts import PermissionSubject


def _context(tmp_path: Path, capabilities: tuple[str, ...]) -> ToolExecutionContext:
    return ToolExecutionContext(
        tmp_path,
        metadata={
            "permission_subject": PermissionSubject(
                "principal",
                "gateway",
                tenant_id="tenant",
                capabilities=capabilities,
            ),
            "session_id": "session",
        },
    )


def test_principal_must_have_every_required_capability(tmp_path: Path) -> None:
    checker = PermissionChecker(PermissionSettingsConfig(mode=PermissionMode.FULL_AUTO))

    denied = checker.evaluate_tool(
        tool_name="robot_go_to",
        is_read_only=False,
        required_capabilities=("tool.mutate", "device.control"),
        arguments={},
        context=_context(tmp_path, ("tool.mutate",)),
    )
    allowed = checker.evaluate_tool(
        tool_name="robot_go_to",
        is_read_only=False,
        required_capabilities=("tool.mutate", "device.control"),
        arguments={},
        context=_context(tmp_path, ("tool.mutate", "device.control")),
    )

    assert denied.allowed is False
    assert "device.control" in denied.reason
    assert allowed.allowed is True


def test_plan_default_and_full_auto_modes_remain_distinct(tmp_path: Path) -> None:
    context = _context(tmp_path, ("tool.mutate",))

    plan = PermissionChecker(PermissionSettingsConfig(mode=PermissionMode.PLAN)).evaluate_tool(
        tool_name="write_file",
        is_read_only=False,
        required_capabilities=("tool.mutate",),
        arguments={},
        context=context,
    )
    default = PermissionChecker(
        PermissionSettingsConfig(mode=PermissionMode.DEFAULT)
    ).evaluate_tool(
        tool_name="write_file",
        is_read_only=False,
        required_capabilities=("tool.mutate",),
        arguments={},
        context=context,
    )
    automatic = PermissionChecker(
        PermissionSettingsConfig(mode=PermissionMode.FULL_AUTO)
    ).evaluate_tool(
        tool_name="write_file",
        is_read_only=False,
        required_capabilities=("tool.mutate",),
        arguments={},
        context=context,
    )

    assert plan.allowed is False and plan.requires_confirmation is False
    assert default.allowed is False and default.requires_confirmation is True
    assert automatic.allowed is True


def test_path_and_command_denies_precede_explicit_allow(tmp_path: Path) -> None:
    checker = PermissionChecker(
        PermissionSettingsConfig(
            mode=PermissionMode.FULL_AUTO,
            allowed_tools=("terminal",),
            denied_commands=("rm -rf *",),
            path_rules=({"pattern": str(tmp_path / "blocked"), "allow": False},),
        )
    )
    context = _context(tmp_path, ("tool.mutate", "process.exec"))

    path = checker.evaluate_tool(
        tool_name="terminal",
        is_read_only=False,
        required_capabilities=("tool.mutate", "process.exec"),
        arguments={"cwd": "blocked"},
        context=context,
    )
    command = checker.evaluate_tool(
        tool_name="terminal",
        is_read_only=False,
        required_capabilities=("tool.mutate", "process.exec"),
        arguments={"command": "rm -rf workspace"},
        context=context,
    )

    assert path.allowed is False and "path matches deny rule" in path.reason
    assert command.allowed is False and "command matches deny rule" in command.reason


def test_terminal_exact_command_allowlist_is_enforced(tmp_path: Path) -> None:
    allowed_command = "grep -Fxq expected fixture && echo OK"
    checker = PermissionChecker(
        PermissionSettingsConfig(
            mode=PermissionMode.FULL_AUTO,
            allowed_terminal_commands=(allowed_command,),
        )
    )
    context = _context(tmp_path, ("tool.mutate", "process.exec"))

    allowed = checker.evaluate_tool(
        tool_name="terminal",
        is_read_only=False,
        required_capabilities=("tool.mutate", "process.exec"),
        arguments={"command": allowed_command},
        context=context,
    )
    altered = checker.evaluate_tool(
        tool_name="terminal",
        is_read_only=False,
        required_capabilities=("tool.mutate", "process.exec"),
        arguments={"command": allowed_command + " "},
        context=context,
    )
    unrelated = checker.evaluate_tool(
        tool_name="echo",
        is_read_only=False,
        required_capabilities=("tool.mutate",),
        arguments={"command": "different"},
        context=context,
    )

    assert allowed.allowed is True
    assert altered.allowed is False and "exact allowlist" in altered.reason
    assert unrelated.allowed is True
