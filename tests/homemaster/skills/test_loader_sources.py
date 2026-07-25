from __future__ import annotations

import json
from pathlib import Path

from homemaster.skills.loader import load_skill_registry

_HOME_TOOLS = (
    "task_interpreter",
    "memory_retriever",
    "target_grounder",
    "load_skill",
    "robot_go_to",
    "observe",
    "robot_manipulate",
    "robot_verify",
    "memory_writer",
    "task_summarizer",
)


def _write_skill(root: Path, name: str, description: str, tool: str = "observe") -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\ntool_names: [{tool!r}]\n---\nbody\n",
        encoding="utf-8",
    )
    return path


def test_source_precedence_and_provenance_are_deterministic(tmp_path: Path) -> None:
    user = tmp_path / "user"
    explicit = tmp_path / "explicit"
    repo = tmp_path / "repo"
    cwd = repo / "package"
    cwd.mkdir(parents=True)
    (repo / ".git").mkdir()
    _write_skill(user, "deploy", "user")
    _write_skill(repo / ".homemaster" / "skills", "deploy", "project")
    _write_skill(explicit, "deploy", "explicit")

    registry = load_skill_registry(
        cwd=cwd,
        user_dirs=(user,),
        explicit_dirs=(explicit,),
    )
    skill = registry.get("deploy")

    assert skill is not None
    assert skill.description == "explicit"
    assert [item.source for item in registry.provenance_for(skill)] == [
        "user",
        "project",
        "explicit",
    ]


def test_legacy_project_skills_directory_is_not_automatically_discovered(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    cwd = repo / "package"
    cwd.mkdir(parents=True)
    (repo / ".git").mkdir()
    _write_skill(repo / "skills", "legacy-root", "must be migrated explicitly")

    registry = load_skill_registry(cwd=cwd)

    assert registry.get("legacy-root") is None


def _write_plugin(
    root: Path,
    plugin_name: str,
    skill_name: str,
    *,
    enabled_by_default: bool = True,
    nested_manifest: bool = False,
    skills_dir: str = "skills",
) -> Path:
    plugin = root / plugin_name
    _write_skill(plugin / skills_dir, skill_name, f"{plugin_name} description")
    manifest = plugin / (".claude-plugin/plugin.json" if nested_manifest else "plugin.json")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "name": plugin_name,
                "enabled_by_default": enabled_by_default,
                "skills_dir": skills_dir,
            }
        ),
        encoding="utf-8",
    )
    return plugin


def test_data_only_plugin_skills_use_manifest_enablement_without_importing_code(
    tmp_path: Path,
) -> None:
    plugins = tmp_path / "plugins"
    enabled = _write_plugin(plugins, "enabled-plugin", "enabled-skill")
    _write_plugin(
        plugins,
        "disabled-plugin",
        "disabled-skill",
        enabled_by_default=False,
        nested_manifest=True,
    )
    sentinel = tmp_path / "plugin-code-ran"
    enabled.joinpath("extension.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('bad')\n",
        encoding="utf-8",
    )

    registry = load_skill_registry(
        plugin_roots=(plugins,),
        enabled_plugins={"disabled-plugin": True},
    )

    enabled_skill = registry.get("enabled-skill")
    assert enabled_skill is not None
    assert registry.get("disabled-skill") is not None
    assert registry.provenance_for(enabled_skill)[-1].source == "plugin"
    assert not sentinel.exists()


def test_project_plugin_skills_are_disabled_by_default_and_require_opt_in(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    cwd = repo / "package"
    cwd.mkdir(parents=True)
    (repo / ".git").mkdir()
    _write_plugin(repo / ".homemaster" / "plugins", "project-plugin", "project-skill")

    disabled = load_skill_registry(cwd=cwd)
    enabled = load_skill_registry(cwd=cwd, allow_project_plugin_skills=True)

    assert disabled.get("project-skill") is None
    project_skill = enabled.get("project-skill")
    assert project_skill is not None
    assert enabled.provenance_for(project_skill)[-1].source == "plugin"


def test_plugin_skills_dir_must_remain_inside_plugin_root(tmp_path: Path) -> None:
    plugins = tmp_path / "plugins"
    plugin = plugins / "unsafe"
    plugin.mkdir(parents=True)
    outside = tmp_path / "outside"
    _write_skill(outside, "escaped", "must not load")
    plugin.joinpath("plugin.json").write_text(
        json.dumps({"name": "unsafe", "skills_dir": "../../outside"}),
        encoding="utf-8",
    )

    registry = load_skill_registry(plugin_roots=(plugins,))

    assert registry.get("escaped") is None
    assert any(
        issue.source == "plugin" and issue.code == "unsafe_root" for issue in registry.issues
    )


def test_plugin_skills_dir_symlink_must_remain_inside_plugin_root(tmp_path: Path) -> None:
    plugins = tmp_path / "plugins"
    plugin = plugins / "unsafe-link"
    plugin.mkdir(parents=True)
    outside = tmp_path / "outside"
    _write_skill(outside, "escaped-link", "must not load")
    plugin.joinpath("skills").symlink_to(outside, target_is_directory=True)
    plugin.joinpath("plugin.json").write_text(
        json.dumps({"name": "unsafe-link", "skills_dir": "skills"}),
        encoding="utf-8",
    )

    registry = load_skill_registry(plugin_roots=(plugins,))

    assert registry.get("escaped-link") is None
    assert any(
        issue.source == "plugin" and issue.code == "unsafe_root" for issue in registry.issues
    )


def test_plugin_cannot_replace_builtin_without_named_authorization(tmp_path: Path) -> None:
    plugins = tmp_path / "plugins"
    _write_plugin(plugins, "override-plugin", "fetch_object")

    registry = load_skill_registry(plugin_roots=(plugins,))

    skill = registry.get("fetch_object")
    assert skill is not None
    assert skill.description != "override-plugin description"
    assert [item.source for item in registry.provenance_for(skill)] == ["builtin"]
    assert any(
        issue.source == "plugin" and issue.code == "invalid_plugin" for issue in registry.issues
    )


def test_plugin_can_replace_builtin_with_named_authorization(tmp_path: Path) -> None:
    plugins = tmp_path / "plugins"
    _write_plugin(plugins, "override-plugin", "fetch_object")

    registry = load_skill_registry(
        plugin_roots=(plugins,),
        allowed_builtin_overrides=("fetch_object",),
    )

    skill = registry.get("fetch_object")
    assert skill is not None
    assert skill.description == "override-plugin description"
    assert [item.source for item in registry.provenance_for(skill)] == ["builtin", "plugin"]


def test_project_discovery_stops_at_git_root(tmp_path: Path) -> None:
    outside = tmp_path / ".homemaster" / "skills"
    _write_skill(outside, "outside", "outside")
    repo = tmp_path / "repo"
    cwd = repo / "nested"
    cwd.mkdir(parents=True)
    (repo / ".git").mkdir()
    _write_skill(repo / ".homemaster" / "skills", "inside", "inside")

    registry = load_skill_registry(cwd=cwd)

    assert registry.get("inside") is not None
    assert registry.get("outside") is None


def test_explicit_source_can_replace_builtin_only_with_recorded_provenance(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "explicit"
    _write_skill(explicit, "fetch_object", "approved replacement")

    registry = load_skill_registry(
        explicit_dirs=(explicit,),
        allowed_builtin_overrides=("fetch_object",),
    )

    skill = registry.get("fetch_object")
    assert skill is not None
    assert skill.description == "approved replacement"
    assert [item.source for item in registry.provenance_for(skill)] == ["builtin", "explicit"]


def test_explicit_source_cannot_replace_builtin_without_named_authorization(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "explicit"
    _write_skill(explicit, "fetch_object", "unapproved replacement")

    try:
        load_skill_registry(
            explicit_dirs=(explicit,),
        )
    except ValueError as exc:
        assert "cannot override builtin" in str(exc)
    else:
        raise AssertionError("builtin replacement must require named authorization")


def test_automatic_skill_text_cannot_grant_an_unavailable_tool(
    tmp_path: Path,
) -> None:
    user = tmp_path / "user"
    _write_skill(user, "unsafe", "unsafe", tool="robot_admin")

    registry = load_skill_registry(user_dirs=(user,))

    assert registry.get("fetch_object") is not None
    unsafe = registry.get("unsafe")
    assert unsafe is not None
    assert "robot_admin" in unsafe.content


def test_frontmatter_invocation_metadata_and_command_alias_are_preserved(
    tmp_path: Path,
) -> None:
    user = tmp_path / "user"
    path = user / "deploy-flow" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        """---
name: Deploy Flow
description: Release deployment workflow.
tool_names: [observe]
user-invocable: false
disable-model-invocation: true
model: test-model
argument-hint: ENV
---
body
""",
        encoding="utf-8",
    )

    registry = load_skill_registry(
        user_dirs=(user,),
    )
    skill = registry.get("deploy-flow")

    assert skill is registry.get("Deploy Flow")
    assert skill is not None
    assert skill.command_name == "deploy-flow"
    assert skill.display_name == "Deploy Flow"
    assert skill.user_invocable is False
    assert skill.disable_model_invocation is True
    assert skill.model == "test-model"
    assert skill.argument_hint == "ENV"
    assert registry.get_model_visible("deploy-flow") is None


def test_automatic_alias_conflict_does_not_partially_register_rejected_skill(
    tmp_path: Path,
) -> None:
    user = tmp_path / "user"
    first = _write_skill(user, "first", "first")
    second = _write_skill(user, "second", "second")
    first.write_text(
        first.read_text(encoding="utf-8").replace("---\nbody", "aliases: [shared]\n---\nbody"),
        encoding="utf-8",
    )
    second.write_text(
        second.read_text(encoding="utf-8").replace("---\nbody", "aliases: [shared]\n---\nbody"),
        encoding="utf-8",
    )

    registry = load_skill_registry(
        user_dirs=(user,),
    )

    assert registry.get("first") is not None
    assert registry.get("shared") is registry.get("first")
    assert registry.get("second") is None
    assert any(issue.code == "invalid_skill" for issue in registry.issues)
