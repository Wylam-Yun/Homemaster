"""Skill discovery with provenance, precedence, and path containment."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from homemaster.skills._frontmatter import parse_skill_metadata
from homemaster.skills.registry import SkillLoadIssue, SkillRegistry
from homemaster.skills.spec import SkillProvenance, SkillSource, SkillSpec

_BUILTIN_DIR = Path(__file__).parent / "builtin"
_DEFAULT_PROJECT_DIRS = (".homemaster/skills", ".agents/skills", ".claude/skills")
_DEFAULT_USER_DIRS = (
    Path("~/.homemaster/skills"),
    Path("~/.agents/skills"),
    Path("~/.claude/skills"),
)


class SkillLoader:
    """Load validated SKILL.md packages from an authorized root."""

    def load_from_file(
        self,
        path: Path,
        *,
        source: SkillSource = "explicit",
        root: Path | None = None,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> SkillSpec:
        resolved_path = path.expanduser().resolve(strict=True)
        resolved_root = (root or path.parent).expanduser().resolve(strict=True)
        _require_within(resolved_path, resolved_root)
        raw = resolved_path.read_text(encoding="utf-8")
        parsed = parse_skill_metadata(resolved_path.parent.name, raw)
        meta = dict(parsed["frontmatter"])
        meta.setdefault("name", parsed["name"])
        meta.setdefault("description", parsed["description"])
        meta["metadata"] = dict(parsed["frontmatter"])
        meta["system_prompt_fragment"] = str(parsed["body"]).strip() or None
        meta["content_path"] = resolved_path
        meta["resource_root"] = resolved_path.parent
        meta["command_name"] = resolved_path.parent.name
        meta["display_name"] = (
            str(parsed["name"]) if str(parsed["name"]) != resolved_path.parent.name else None
        )
        meta["aliases"] = _string_tuple(meta.get("aliases"), field="aliases")
        meta["user_invocable"] = _frontmatter_bool(
            meta.get("user-invocable"), default=True, field="user-invocable"
        )
        meta["disable_model_invocation"] = _frontmatter_bool(
            meta.get("disable-model-invocation"),
            default=False,
            field="disable-model-invocation",
        )
        meta["model"] = _optional_text(meta.get("model"))
        meta["argument_hint"] = _optional_text(meta.get("argument-hint"))
        meta["source"] = source
        meta["provenance"] = (
            SkillProvenance(source=source, path=resolved_path, root=resolved_root),
        )
        spec = SkillSpec.model_validate(meta)
        if allowed_tool_names is not None:
            allowed = frozenset(allowed_tool_names)
            unknown = sorted(set(spec.tool_names) - allowed)
            if unknown:
                raise ValueError(
                    f"skill {spec.name!r} references tools outside the frozen ToolView: {unknown}"
                )
        return spec

    def load_builtin(
        self,
        name: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> SkillSpec:
        skill_path = _BUILTIN_DIR / name / "SKILL.md"
        if not skill_path.exists():
            raise FileNotFoundError(f"Builtin skill not found: {skill_path}")
        return self.load_from_file(
            skill_path,
            source="builtin",
            root=_BUILTIN_DIR,
            allowed_tool_names=allowed_tool_names,
        )

    def resolve_resource(self, spec: SkillSpec, relative_path: str | Path) -> Path:
        """Resolve a skill resource without allowing absolute or symlink escape."""

        if spec.resource_root is None:
            raise ValueError(f"skill {spec.name!r} has no resource root")
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("skill resource path must be relative and contained")
        root = spec.resource_root.resolve(strict=True)
        resource = (root / relative).resolve(strict=True)
        _require_within(resource, root)
        return resource


def load_builtin_skills(
    registry: Any,
    *,
    allowed_tool_names: Iterable[str] | None = None,
) -> None:
    loader = SkillLoader()
    for path in sorted(_BUILTIN_DIR.glob("*/SKILL.md")):
        registry.register(
            loader.load_builtin(path.parent.name, allowed_tool_names=allowed_tool_names)
        )


def load_skill_registry(
    *,
    cwd: Path | None = None,
    user_dirs: Iterable[Path] = _DEFAULT_USER_DIRS,
    project_dirs: Iterable[str] = _DEFAULT_PROJECT_DIRS,
    explicit_dirs: Iterable[Path] = (),
    allowed_tool_names: Iterable[str] | None = None,
    allow_project: bool = True,
    allowed_builtin_overrides: Iterable[str] = (),
) -> SkillRegistry:
    """Load builtin < user < project < explicit sources deterministically."""

    registry = SkillRegistry()
    load_builtin_skills(registry, allowed_tool_names=allowed_tool_names)
    _load_automatic_sources(
        registry,
        user_dirs,
        source="user",
        allowed_tool_names=allowed_tool_names,
    )
    if cwd is not None and allow_project:
        discovered, discovery_issues = _discover_project_skill_dirs(cwd, project_dirs)
        for issue in discovery_issues:
            registry.record_issue(issue)
        _load_automatic_sources(
            registry,
            discovered,
            source="project",
            allowed_tool_names=allowed_tool_names,
        )
    authorized = frozenset(allowed_builtin_overrides)
    for spec in load_skills_from_dirs(
        explicit_dirs, source="explicit", allowed_tool_names=allowed_tool_names
    ):
        registry.register(spec, allow_builtin_override=spec.name in authorized)
    return registry


def load_skills_from_dirs(
    directories: Iterable[Path],
    *,
    source: SkillSource,
    allowed_tool_names: Iterable[str] | None = None,
) -> list[SkillSpec]:
    loader = SkillLoader()
    skills: list[SkillSpec] = []
    for raw_root in directories:
        expanded = raw_root.expanduser()
        if not expanded.is_dir():
            continue
        root = expanded.resolve(strict=True)
        for path in sorted(root.glob("*/SKILL.md")):
            skills.append(
                loader.load_from_file(
                    path,
                    source=source,
                    root=root,
                    allowed_tool_names=allowed_tool_names,
                )
            )
    return skills


def discover_project_skill_dirs(cwd: Path, relative_dirs: Iterable[str]) -> list[Path]:
    """Discover contained project skill roots from git root toward cwd."""

    roots, _ = _discover_project_skill_dirs(cwd, relative_dirs)
    return roots


def _discover_project_skill_dirs(
    cwd: Path,
    relative_dirs: Iterable[str],
) -> tuple[list[Path], list[SkillLoadIssue]]:
    """Return project roots and secret-safe diagnostics for rejected roots."""

    start = cwd.expanduser().resolve(strict=True)
    if start.is_file():
        start = start.parent
    git_root = _find_git_root(start)
    if git_root is None:
        return [], []
    safe_relatives: list[Path] = []
    for value in relative_dirs:
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            continue
        safe_relatives.append(relative)
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
            if candidate.is_dir():
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


def _load_automatic_sources(
    registry: SkillRegistry,
    directories: Iterable[Path],
    *,
    source: SkillSource,
    allowed_tool_names: Iterable[str] | None,
) -> None:
    loader = SkillLoader()
    for raw_root in directories:
        expanded = raw_root.expanduser()
        if not expanded.is_dir():
            continue
        root = expanded.resolve(strict=True)
        for path in sorted(root.glob("*/SKILL.md")):
            try:
                spec = loader.load_from_file(
                    path,
                    source=source,
                    root=root,
                    allowed_tool_names=allowed_tool_names,
                )
                registry.register(spec)
            except (OSError, ValueError) as exc:
                registry.record_issue(
                    SkillLoadIssue(
                        source=source,
                        code="invalid_skill",
                        detail=f"{type(exc).__name__}: rejected by skill validation",
                    )
                )


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
        raise ValueError(f"skill path escapes authorized root: {path}") from exc


def _frontmatter_bool(value: Any, *, default: bool, field: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
    raise ValueError(f"SKILL.md {field} must be a boolean")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_tuple(value: Any, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (list, tuple)):
        values = tuple(value)
    else:
        raise ValueError(f"SKILL.md {field} must be a string or list of strings")
    normalized = tuple(str(item).strip() for item in values if str(item).strip())
    return normalized
