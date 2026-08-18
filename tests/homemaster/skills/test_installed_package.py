from __future__ import annotations

import re
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path


def _requirement_name(requirement: str) -> str:
    return re.split(r"[<>=!~; \[]", requirement, maxsplit=1)[0].lower()


def test_homemaster_declares_vendored_mindmemos_runtime_dependencies() -> None:
    repo = Path(__file__).resolve().parents[3]
    homemaster = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    mindmemos = tomllib.loads(
        (repo / "third_party/MindMemOS/src/mindmemos/pyproject.toml").read_text(encoding="utf-8")
    )
    declared = {
        _requirement_name(requirement) for requirement in homemaster["project"]["dependencies"]
    }
    required = {
        _requirement_name(requirement) for requirement in mindmemos["project"]["dependencies"]
    }
    assert required <= declared
    assert homemaster["project"]["requires-python"] == ">=3.11,<3.14"


def test_built_wheel_exposes_builtin_skills_outside_source_checkout(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[3]
    dist = tmp_path / "dist"
    venv = tmp_path / "venv"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist)],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(dist.glob("homemaster-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "mindmemos/__init__.py" in names
        assert "homemaster/memory/mindmemos_runtime.py" in names
        assert "homemaster/memory/mindmemos_entity_modeling.json" in names
        assert not any(name.startswith("mem0/") for name in names)
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")
    assert "Requires-Dist: mem0ai" not in metadata
    assert (
        "Requires-Dist: en-core-web-sm @ https://github.com/explosion/spacy-models/releases/"
        "download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl" in metadata
    )
    subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(venv)],
        check=True,
        capture_output=True,
        text=True,
    )
    python = venv / "bin" / "python"
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )
    probe = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            (
                "from importlib.resources import files; "
                "r=files('homemaster.skills').joinpath('builtin'); "
                "assert r.joinpath('fetch_object','SKILL.md').is_file(); "
                "assert r.joinpath('check_object_state','SKILL.md').is_file(); "
                "assert r.joinpath('change-ticket-executor','SKILL.md').is_file(); "
                "b=files('homemaster.skills').joinpath('bundled','content'); "
                "expected={'commit.md','debug.md','diagnose.md','plan.md','review.md',"
                "'simplify.md','skill-creator.md','test.md'}; "
                "assert {p.name for p in b.iterdir() if p.is_file()} == expected; "
                "from homemaster.adapters import build_universal_tool_registry; "
                "tools=set('terminal ask_user_question read_file write_file edit_file "
                "notebook_edit "
                "lsp mcp_auth search_files image_to_text image_generation load_skill tool_search "
                "web_fetch web_search config brief sleep enter_worktree exit_worktree todo_write "
                "enter_plan_mode exit_plan_mode cron_create cron_list cron_delete cron_toggle "
                "remote_trigger task_create task_get task_list task_stop task_output task_update "
                "agent send_message team_create team_delete'.split()); "
                "assert len(tools) == 38; "
                "names=set(build_universal_tool_registry().all_names()); "
                "assert tools <= names; "
                "assert {'skill','skill_view'}.isdisjoint(names); "
                "import mindmemos; "
                "assert mindmemos.__file__; "
                "from mindmemos.pipelines import create_pipeline; "
                "from mindmemos.typing import (AddPipelineInput, DialogueMessage, "
                "DreamingActionReceipt, DreamingPipelineInput, DreamingPipelineResult); "
                "assert callable(create_pipeline); "
                "assert AddPipelineInput(messages=[DialogueMessage(role='user', content='hi')]); "
                "assert DreamingPipelineInput(seed_add_record_ids=['add-1']); "
                "assert DreamingPipelineResult(status='ok', outcome='no_action'); "
                "assert DreamingActionReceipt(action='archive', status='ok'); "
                "from homemaster.memory.feedback_context import FeedbackContextSnapshot; "
                "from homemaster.experience import DreamingStateStore; "
                "assert FeedbackContextSnapshot(messages=(), recalled_memories=()); "
                "assert DreamingStateStore; "
                "from homemaster.adapters.profiles import build_tool_registry; "
                "home_names=build_tool_registry(environment=None).all_names(); "
                "assert home_names.count('mindmemos_feedback') == 1; "
                "assert [name for name in home_names if 'feedback' in name] == "
                "['mindmemos_feedback']; "
                "print('PASS')"
            ),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert probe.stdout.strip() == "PASS"
