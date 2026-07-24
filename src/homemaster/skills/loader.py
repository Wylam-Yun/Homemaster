"""Skill discovery with OpenHarness definitions and HomeMaster source policy."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from homemaster.skills._frontmatter import (
    optional_frontmatter_str,
    parse_bool_frontmatter,
    parse_skill_metadata,
)
from homemaster.skills.bundled import get_bundled_skills
from homemaster.skills.registry import SkillLoadIssue, SkillProvenance, SkillRegistry
from homemaster.skills.types import SkillDefinition

_BUILTIN_DIR = Path(__file__).parent / "builtin"
_DEFAULT_PROJECT_DIRS = (".homemaster/skills",)
_DEFAULT_USER_DIRS = (Path("~/.homemaster/skills"),)


class _UnsafeSkillRootError(ValueError):
    pass


class SkillLoader:
    """Load OpenHarness-format SKILL.md files from an authorized root."""

    def load_from_file(
        self,
        path: Path,
        *,
        source: str = "explicit",
        root: Path | None = None,
    ) -> SkillDefinition:
        resolved_path = path.expanduser().resolve(strict=True)
        resolved_root = (root or path.parent).expanduser().resolve(strict=True)
        _require_within(resolved_path, resolved_root)
        return self._load_content(
            default_name=resolved_path.parent.name,
            content=resolved_path.read_text(encoding="utf-8"),
            source=source,
            path=resolved_path,
            base_dir=resolved_path.parent,
        )

    def load_builtin(self, name: str) -> SkillDefinition:
        path = _BUILTIN_DIR / name / "SKILL.md"
        if not path.exists():
            raise FileNotFoundError(f"Builtin skill not found: {path}")
        return self.load_from_file(path, source="builtin", root=_BUILTIN_DIR)

    def resolve_resource(self, skill: SkillDefinition, relative_path: str | Path) -> Path:
        """Resolve a Skill resource without absolute, parent, or symlink escape."""

        if skill.base_dir is None:
            raise ValueError(f"skill {skill.name!r} has no base directory")
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("skill resource path must be relative and contained")
        root = Path(skill.base_dir).resolve(strict=True)
        resource = (root / relative).resolve(strict=True)
        _require_within(resource, root)
        return resource

    @staticmethod
    def _load_content(
        *,
        default_name: str,
        content: str,
        source: str,
        path: Path,
        base_dir: Path,
    ) -> SkillDefinition:
        parsed = parse_skill_metadata(default_name, content, fallback_template="Skill: {name}")
        frontmatter = parsed.get("frontmatter")
        if not isinstance(frontmatter, dict):
            frontmatter = {}
        name = str(parsed["name"])
        return SkillDefinition(
            name=name,
            description=str(parsed["description"]),
            content=content,
            source=source,
            path=str(path),
            base_dir=str(base_dir),
            command_name=default_name,
            display_name=name if name != default_name else None,
            aliases=_string_tuple(frontmatter.get("aliases")),
            user_invocable=parse_bool_frontmatter(frontmatter.get("user-invocable"), default=True),
            disable_model_invocation=parse_bool_frontmatter(
                frontmatter.get("disable-model-invocation"), default=False
            ),
            model=optional_frontmatter_str(frontmatter.get("model")),
            argument_hint=optional_frontmatter_str(frontmatter.get("argument-hint")),
        )


def load_bundled_skills(registry: SkillRegistry) -> None:
    content_root = Path(__file__).parent / "bundled" / "content"
    for skill in get_bundled_skills():
        assert skill.path is not None
        registry.register(
            skill,
            provenance=SkillProvenance(source="bundled", path=Path(skill.path), root=content_root),
        )


def load_builtin_skills(registry: SkillRegistry) -> None:
    loader = SkillLoader()
    for path in sorted(_BUILTIN_DIR.glob("*/SKILL.md")):
        skill = loader.load_builtin(path.parent.name)
        registry.register(
            skill,
            provenance=SkillProvenance(source="builtin", path=path.resolve(), root=_BUILTIN_DIR),
        )


def load_skill_registry(
    *,
    cwd: Path | None = None,
    user_dirs: Iterable[Path] = _DEFAULT_USER_DIRS,
    project_dirs: Iterable[str] = _DEFAULT_PROJECT_DIRS,
    explicit_dirs: Iterable[Path] = (),
    allow_project: bool = True,
    plugin_roots: Iterable[Path] = (),
    enabled_plugins: dict[str, bool] | None = None,
    allow_project_plugin_skills: bool = False,
    allowed_builtin_overrides: Iterable[str] = (),
) -> SkillRegistry:
    """Load bundled < Home builtin < user < project < explicit < plugin Skills."""

    registry = SkillRegistry()
    authorized = frozenset(allowed_builtin_overrides)
    load_bundled_skills(registry)
    load_builtin_skills(registry)
    _load_automatic_sources(
        registry,
        user_dirs,
        source="user",
        allowed_builtin_overrides=authorized,
    )
    if cwd is not None and allow_project:
        discovered, issues = _discover_project_skill_dirs(cwd, project_dirs)
        for issue in issues:
            registry.record_issue(issue)
        _load_automatic_sources(
            registry,
            discovered,
            source="project",
            allowed_builtin_overrides=authorized,
        )
    for skill, provenance in _load_from_dirs(explicit_dirs, source="explicit"):
        registry.register(
            skill,
            provenance=provenance,
            allow_builtin_override=skill.name in authorized,
        )
    roots = list(plugin_roots)
    if cwd is not None and allow_project_plugin_skills:
        project_root = _find_git_root(cwd.expanduser().resolve(strict=True))
        if project_root is not None:
            roots.append(project_root / ".openharness" / "plugins")
    _load_plugin_skill_sources(
        registry,
        roots,
        enabled_plugins=enabled_plugins or {},
        allowed_builtin_overrides=authorized,
    )
    return registry


def _load_plugin_skill_sources(
    registry: SkillRegistry,
    roots: Iterable[Path],
    *,
    enabled_plugins: dict[str, bool],
    allowed_builtin_overrides: frozenset[str],
) -> None:
    for raw_root in roots:
        expanded = raw_root.expanduser()
        if not expanded.is_dir():
            continue
        try:
            root = expanded.resolve(strict=True)
        except OSError as exc:
            registry.record_issue(
                SkillLoadIssue(source="plugin", code="invalid_plugin", detail=type(exc).__name__)
            )
            continue
        for raw_plugin in sorted(path for path in root.iterdir() if path.is_dir()):
            try:
                plugin = raw_plugin.resolve(strict=True)
                _require_within(plugin, root)
                manifest_path = _find_plugin_manifest(plugin)
                if manifest_path is None:
                    continue
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(manifest, dict):
                    raise ValueError("plugin manifest must be an object")
                name = manifest.get("name")
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("plugin manifest name must be non-empty")
                enabled_by_default = manifest.get("enabled_by_default", True)
                if not isinstance(enabled_by_default, bool):
                    raise ValueError("enabled_by_default must be a boolean")
                if not enabled_plugins.get(name, enabled_by_default):
                    continue
                skills_dir = manifest.get("skills_dir", "skills")
                if not isinstance(skills_dir, str):
                    raise ValueError("skills_dir must be a string")
                relative = Path(skills_dir)
                if relative.is_absolute() or ".." in relative.parts:
                    raise _UnsafeSkillRootError("unsafe plugin skills_dir")
                skill_root = (plugin / relative).resolve(strict=True)
                _require_within(skill_root, plugin)
                for skill, provenance in _load_from_dirs((skill_root,), source="plugin"):
                    registry.register(
                        skill,
                        provenance=provenance,
                        allow_builtin_override=skill.name in allowed_builtin_overrides,
                    )
            except _UnsafeSkillRootError as exc:
                registry.record_issue(
                    SkillLoadIssue(source="plugin", code="unsafe_root", detail=type(exc).__name__)
                )
            except (json.JSONDecodeError, OSError, ValueError) as exc:
                registry.record_issue(
                    SkillLoadIssue(
                        source="plugin", code="invalid_plugin", detail=type(exc).__name__
                    )
                )


def _find_plugin_manifest(plugin: Path) -> Path | None:
    for candidate in (plugin / "plugin.json", plugin / ".claude-plugin" / "plugin.json"):
        if candidate.is_file():
            resolved = candidate.resolve(strict=True)
            _require_within(resolved, plugin)
            return resolved
    return None


def load_skills_from_dirs(directories: Iterable[Path], *, source: str) -> list[SkillDefinition]:
    return [skill for skill, _provenance in _load_from_dirs(directories, source=source)]


def discover_project_skill_dirs(cwd: Path, relative_dirs: Iterable[str]) -> list[Path]:
    roots, _issues = _discover_project_skill_dirs(cwd, relative_dirs)
    return roots


def _load_from_dirs(
    directories: Iterable[Path], *, source: str
) -> list[tuple[SkillDefinition, SkillProvenance]]:
    loader = SkillLoader()
    loaded: list[tuple[SkillDefinition, SkillProvenance]] = []
    for raw_root in directories:
        expanded = raw_root.expanduser()
        if not expanded.is_dir():
            continue
        root = expanded.resolve(strict=True)
        for path in sorted(root.glob("*/SKILL.md")):
            skill = loader.load_from_file(path, source=source, root=root)
            loaded.append((skill, SkillProvenance(source=source, path=path.resolve(), root=root)))
    return loaded


def _load_automatic_sources(
    registry: SkillRegistry,
    directories: Iterable[Path],
    *,
    source: str,
    allowed_builtin_overrides: frozenset[str],
) -> None:
    loader = SkillLoader()
    for raw_root in directories:
        expanded = raw_root.expanduser()
        if not expanded.is_dir():
            continue
        try:
            root = expanded.resolve(strict=True)
        except OSError as exc:
            registry.record_issue(
                SkillLoadIssue(source=source, code="invalid_skill", detail=type(exc).__name__)
            )
            continue
        for path in sorted(root.glob("*/SKILL.md")):
            try:
                skill = loader.load_from_file(path, source=source, root=root)
                registry.register(
                    skill,
                    provenance=SkillProvenance(source=source, path=path.resolve(), root=root),
                    allow_builtin_override=skill.name in allowed_builtin_overrides,
                )
            except (OSError, ValueError) as exc:
                registry.record_issue(
                    SkillLoadIssue(source=source, code="invalid_skill", detail=type(exc).__name__)
                )


def _discover_project_skill_dirs(
    cwd: Path,
    relative_dirs: Iterable[str],
) -> tuple[list[Path], list[SkillLoadIssue]]:
    start = cwd.expanduser().resolve(strict=True)
    if start.is_file():
        start = start.parent
    git_root = _find_git_root(start)
    if git_root is None:
        return [], []
    safe_relatives = [
        relative
        for value in relative_dirs
        if not (relative := Path(value)).is_absolute() and ".." not in relative.parts
    ]
    levels: list[Path] = []
    current = start
    while True:
        levels.append(current)
        if current == git_root:
            break
        current = current.parent
    roots: list[Path] = []
    issues: list[SkillLoadIssue] = []
    for level in reversed(levels):
        for relative in safe_relatives:
            candidate = level / relative
            if not candidate.is_dir():
                continue
            resolved = candidate.resolve(strict=True)
            try:
                _require_within(resolved, git_root)
            except ValueError:
                issues.append(
                    SkillLoadIssue(
                        source="project",
                        code="unsafe_root",
                        detail="project skill root resolves outside the git root",
                    )
                )
                continue
            if resolved not in roots:
                roots.append(resolved)
    return roots, issues


def _find_git_root(start: Path) -> Path | None:
    current = start
    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def _require_within(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise _UnsafeSkillRootError(f"skill path escapes authorized root: {path}") from exc


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (list, tuple)):
        values = tuple(value)
    else:
        raise ValueError("SKILL.md aliases must be a string or list of strings")
    return tuple(str(item).strip() for item in values if str(item).strip())


__all__ = [
    "SkillLoader",
    "discover_project_skill_dirs",
    "load_builtin_skills",
    "load_bundled_skills",
    "load_skill_registry",
    "load_skills_from_dirs",
]
