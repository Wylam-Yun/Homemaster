"""Red tests for the V2.0 OpenHarness Skill compatibility port."""

from pathlib import Path

import pytest

from homemaster.adapters.profiles import build_home_profile
from homemaster.agent.messages import ToolCall
from homemaster.agent.normalized import RunContext
from homemaster.skills.loader import load_skill_registry
from homemaster.tools.contracts import PermissionSubject, ToolExecutionContext, ToolExecutionStatus
from homemaster.tools.legacy_adapter import LegacyToolExecutionContext
from homemaster.tools.pipeline import ToolExecutionPipeline

_STANDARD_SKILL = """---
name: standard-fixture
description: A standard Skill that intentionally declares no tool names.
user-invocable: true
argument-hint: "[topic]"
---

# Standard fixture

Read `references/example.md`, then use the ordinary tools available to the run.
"""


def _write_skill(root: Path, name: str = "standard-fixture") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    path = skill_dir / "SKILL.md"
    path.write_text(_STANDARD_SKILL, encoding="utf-8")
    return path


def test_standard_skill_preserves_full_markdown_without_tool_names(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    path = _write_skill(root)

    registry = load_skill_registry(user_dirs=(root,), allow_project=False)

    skill = registry.get("standard-fixture")
    assert skill is not None
    assert skill.content == _STANDARD_SKILL
    assert Path(skill.base_dir) == path.parent
    assert skill.command_name == "standard-fixture"


def test_reloading_discovers_a_complete_new_standard_skill(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    first = load_skill_registry(user_dirs=(root,), allow_project=False)
    assert first.get("standard-fixture") is None

    _write_skill(root)

    refreshed = load_skill_registry(user_dirs=(root,), allow_project=False)
    skill = refreshed.get("standard-fixture")
    assert skill is not None
    assert skill.content == _STANDARD_SKILL


@pytest.mark.asyncio
async def test_standard_skill_tool_refreshes_then_returns_complete_content(tmp_path: Path) -> None:
    root = tmp_path / "skills"

    def discover():
        return load_skill_registry(user_dirs=(root,), allow_project=False)

    registry = discover()
    registry.set_refresher(discover)
    path = _write_skill(root)

    profile = build_home_profile()
    tool = profile.view.lookup("skill").tool
    assert tool is not None
    run_context = RunContext(
        session_id="session",
        run_id="run",
        turn_index=0,
        settings=object(),
        event_sink=None,
        deps={"skill_registry": registry},
    )
    context = ToolExecutionContext(
        session_id="session",
        run_id="run",
        turn_index=0,
        tool_call_id="call-1",
        internal_tool_id=tool.definition.internal_id,
        tool_view=profile.view,
        permission_subject=PermissionSubject(
            subject_id="operator",
            channel="test",
            capabilities=("tool.read",),
        ),
        backend=LegacyToolExecutionContext(
            run_context=run_context,
            tool_call_id="call-1",
            internal_tool_id=tool.definition.internal_id,
        ),
        deadline=None,
        cancellation=None,
        domain_observer=None,
        working_directory=Path.cwd(),
    )

    result = await ToolExecutionPipeline(profile.catalog).execute(
        ToolCall(id="call-1", name="skill", arguments={"name": "standard-fixture"}),
        context,
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data["content"] == path.read_text(encoding="utf-8")
    assert result.data["base_dir"] == str(path.parent.resolve())
