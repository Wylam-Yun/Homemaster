#!/usr/bin/env python3
"""Generate or verify the immutable V2.0 OpenHarness port manifest."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
from collections import deque
from pathlib import Path

COMMIT = "9b2efd795c6aa09f88b0c257d269a9e518da6ae7"
TOOL_PATHS = (
    ("bash", "tools/bash_tool.py"),
    ("ask_user_question", "tools/ask_user_question_tool.py"),
    ("read_file", "tools/file_read_tool.py"),
    ("write_file", "tools/file_write_tool.py"),
    ("edit_file", "tools/file_edit_tool.py"),
    ("notebook_edit", "tools/notebook_edit_tool.py"),
    ("lsp", "tools/lsp_tool.py"),
    ("mcp_auth", "tools/mcp_auth_tool.py"),
    ("glob", "tools/glob_tool.py"),
    ("grep", "tools/grep_tool.py"),
    ("image_to_text", "tools/image_to_text_tool.py"),
    ("image_generation", "tools/image_generation_tool.py"),
    ("skill", "tools/skill_tool.py"),
    ("tool_search", "tools/tool_search_tool.py"),
    ("web_fetch", "tools/web_fetch_tool.py"),
    ("web_search", "tools/web_search_tool.py"),
    ("config", "tools/config_tool.py"),
    ("brief", "tools/brief_tool.py"),
    ("sleep", "tools/sleep_tool.py"),
    ("enter_worktree", "tools/enter_worktree_tool.py"),
    ("exit_worktree", "tools/exit_worktree_tool.py"),
    ("todo_write", "tools/todo_write_tool.py"),
    ("enter_plan_mode", "tools/enter_plan_mode_tool.py"),
    ("exit_plan_mode", "tools/exit_plan_mode_tool.py"),
    ("cron_create", "tools/cron_create_tool.py"),
    ("cron_list", "tools/cron_list_tool.py"),
    ("cron_delete", "tools/cron_delete_tool.py"),
    ("cron_toggle", "tools/cron_toggle_tool.py"),
    ("remote_trigger", "tools/remote_trigger_tool.py"),
    ("task_create", "tools/task_create_tool.py"),
    ("task_get", "tools/task_get_tool.py"),
    ("task_list", "tools/task_list_tool.py"),
    ("task_stop", "tools/task_stop_tool.py"),
    ("task_output", "tools/task_output_tool.py"),
    ("task_update", "tools/task_update_tool.py"),
    ("agent", "tools/agent_tool.py"),
    ("send_message", "tools/send_message_tool.py"),
    ("team_create", "tools/team_create_tool.py"),
    ("team_delete", "tools/team_delete_tool.py"),
)
ROOTS = tuple(path for _, path in TOOL_PATHS) + (
    "tools/__init__.py",
    "tools/base.py",
    "skills/_frontmatter.py",
    "skills/types.py",
    "skills/registry.py",
    "skills/loader.py",
    "skills/bundled/__init__.py",
    "prompts/context.py",
)
TEST_PATHS = (
    "tests/test_skills/test_loader.py",
    "tests/test_tools/test_bash_tool.py",
    "tests/test_tools/test_core_tools.py",
    "tests/test_tools/test_grep_tool.py",
    "tests/test_tools/test_image_generation_tool.py",
    "tests/test_tools/test_image_to_text_tool.py",
    "tests/test_tools/test_integration_flows.py",
    "tests/test_tools/test_mcp_auth_tool.py",
    "tests/test_tools/test_mcp_tool.py",
    "tests/test_tools/test_task_tools.py",
    "tests/test_tools/test_web_fetch_tool.py",
    "tests/test_mcp/test_stdio_flow.py",
    "tests/test_mcp/test_http_flow.py",
    "tests/test_mcp/test_integration.py",
    "tests/test_swarm/test_imports.py",
)
TEST_SUPPORT_PATHS = ("tests/fixtures/fake_mcp_server.py",)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("plan/V2.0/upstream-port-manifest.json"),
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    manifest = _manifest(args.upstream.resolve())
    rendered = json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit("V2.0 upstream port manifest is stale")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


def _manifest(upstream: Path) -> dict[str, object]:
    if _commit(upstream) != COMMIT:
        raise ValueError("upstream checkout does not match the locked V2.0 commit")
    package_root = upstream / "src" / "openharness"
    test_imports = _test_import_roots(upstream, TEST_PATHS)
    closure = _import_closure(package_root, ROOTS, root_modules=test_imports)
    bundled = sorted((package_root / "skills" / "bundled" / "content").glob("*.md"))
    return {
        "schema_version": "homemaster-v2.0-upstream-port-manifest-v1",
        "upstream": {
            "repo": "https://github.com/HKUDS/OpenHarness.git",
            "commit": COMMIT,
        },
        "default_tool_order": [
            {"name": name, "path": f"src/openharness/{path}", "sha256": _sha(package_root / path)}
            for name, path in TOOL_PATHS
        ],
        "source_roots": [
            {"path": f"src/openharness/{path}", "sha256": _sha(package_root / path)}
            for path in ROOTS
        ],
        "static_import_closure": [
            {"path": f"src/openharness/{path}", "sha256": _sha(package_root / path)}
            for path in closure
        ],
        "bundled_skills": [
            {
                "path": f"src/openharness/skills/bundled/content/{path.name}",
                "sha256": _sha(path),
            }
            for path in bundled
        ],
        "upstream_test_files": [
            {"path": path, "sha256": _sha(upstream / path)} for path in TEST_PATHS
        ],
        "upstream_test_support_files": [
            {"path": path, "sha256": _sha(upstream / path)} for path in TEST_SUPPORT_PATHS
        ],
        "completion_rule": (
            "No default tool is complete until its manifest port record names the copied upstream "
            "test or a test gap, Home adapter deltas, and its per-target black-box gate."
        ),
    }


def _import_closure(
    package_root: Path,
    roots: tuple[str, ...],
    *,
    root_modules: tuple[str, ...] = (),
) -> list[str]:
    modules = _module_paths(package_root)
    pending = deque(_module_name(path) for path in roots)
    pending.extend(root_modules)
    seen: set[str] = set()
    while pending:
        module = pending.popleft()
        if module in seen or module not in modules:
            continue
        seen.add(module)
        path = modules[module]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for dependency in _imports(tree, module):
            if dependency in modules and dependency not in seen:
                pending.append(dependency)
    return sorted(path.relative_to(package_root).as_posix() for module, path in modules.items() if module in seen)


def _test_import_roots(upstream: Path, test_paths: tuple[str, ...]) -> tuple[str, ...]:
    roots: set[str] = set()
    for relative in test_paths:
        path = upstream / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        roots.update(_imports(tree, "tests.placeholder"))
    return tuple(sorted(roots))


def _module_paths(package_root: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for path in package_root.rglob("*.py"):
        relative = path.relative_to(package_root).with_suffix("")
        parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
        module = ".".join(("openharness", *parts))
        paths[module] = path
    return paths


def _module_name(path: str) -> str:
    relative = Path(path).with_suffix("")
    parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
    return ".".join(("openharness", *parts))


def _imports(tree: ast.AST, module: str) -> set[str]:
    found: set[str] = set()
    package = module.rsplit(".", 1)[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if alias.name.startswith("openharness"))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = package.split(".")
                parent = base_parts[: len(base_parts) - node.level + 1]
                base = ".".join((*parent, *(node.module or "").split("."))).rstrip(".")
            else:
                base = node.module or ""
            if base.startswith("openharness"):
                found.add(base)
                found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def _commit(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
