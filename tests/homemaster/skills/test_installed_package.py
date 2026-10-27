from __future__ import annotations

import re
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path


def _requirement_name(requirement: str) -> str:
    return re.split(r"[<>=!~; \[]", requirement, maxsplit=1)[0].lower()


def test_homemaster_declares_only_sdk_tier_dependencies() -> None:
    """V3.8: the memory extra carries the SDK + raw-readback drivers only."""
    repo = Path(__file__).resolve().parents[3]
    homemaster = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {
        _requirement_name(requirement) for requirement in homemaster["project"]["dependencies"]
    }
    memory_extra = {
        _requirement_name(requirement)
        for requirement in homemaster["project"]["optional-dependencies"]["memory"]
    }
    # The mindmemos server package itself must never be a Requires-Dist —
    # HomeMaster talks to the standalone MindMemOS HTTP server via the SDK.
    assert "mindmemos" not in declared | memory_extra
    assert {"mindmemos-sdk", "neo4j", "qdrant-client"} <= memory_extra
    # The server-side extraction deps must not leak into HomeMaster's env.
    server_only = {"jieba", "spacy", "en-core-web-sm", "litellm", "aiokafka", "traced", "omegaconf"}
    assert server_only.isdisjoint(declared | memory_extra)
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
        # V3.8: the server package and embedded lifecycle are gone from the
        # wheel; the SDK client facade ships instead.
        assert not any(name.startswith("mindmemos/") for name in names)
        assert "homemaster/memory/mindmemos_runtime.py" not in names
        assert "homemaster/memory/mindmemos_client.py" in names
        assert not any(name.startswith("mem0/") for name in names)
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")
    assert "Requires-Dist: mem0ai" not in metadata
    assert "Requires-Dist: mindmemos-sdk" in metadata
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
                "assert r.joinpath('change-ticket-executor','SKILL.md').is_file(); "
                "b=files('homemaster.skills').joinpath('bundled','content'); "
                "expected={'commit.md','debug.md','diagnose.md','plan.md','review.md',"
                "'simplify.md','skill-creator.md','test.md'}; "
                "assert {p.name for p in b.iterdir() if p.is_file()} == expected; "
                # V3.8: the lightweight tier installs no MindMemOS packages at
                # all — neither the server nor the SDK/readback drivers.
                "import importlib.util; "
                "assert importlib.util.find_spec('mindmemos') is None; "
                "assert importlib.util.find_spec('mindmemos_sdk') is None; "
                "assert importlib.util.find_spec('qdrant_client') is None; "
                "print('PASS')"
            ),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert probe.stdout.strip() == "PASS"

    # Registry surface stays covered in-process: the bare wheel intentionally
    # ships without the memory extra, while homemaster.adapters ->
    # memory_tools -> mindmemos_client currently imports mindmemos_sdk
    # eagerly, so build_tool_registry() requires the `memory` extra installed
    # (src-side layering is outside the scope of this test-only change).
    from homemaster.adapters import build_tool_registry

    tools = set(
        "terminal ask_user_question read_file write_file edit_file notebook_edit "
        "lsp mcp_auth search_files image_to_text image_generation load_skill tool_search "
        "web_fetch web_search config brief sleep enter_worktree exit_worktree todo_write "
        "enter_plan_mode exit_plan_mode cron_create cron_list cron_delete cron_toggle "
        "remote_trigger task_create task_get task_list task_stop task_output task_update "
        "agent send_message team_create team_delete".split()
    )
    assert len(tools) == 38
    names = set(build_tool_registry(environment="local_robot").all_names())
    assert tools <= names
    assert {"skill", "skill_view"}.isdisjoint(names)

    from homemaster.adapters.profiles import build_tool_registry as build_profiles

    home_names = build_profiles(environment=None).all_names()
    assert "context_memory" in home_names
    files_names = build_profiles(environment=None, memory_tier="files").all_names()
    assert "context_memory" in files_names
    assert not any("mindmemos" in n for n in files_names)
