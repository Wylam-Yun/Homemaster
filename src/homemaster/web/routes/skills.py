"""Skill routes: list the registry and resolve slash commands server-side."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, Response

from homemaster.config.config import _default_skill_user_dirs
from homemaster.skills._frontmatter import parse_skill_metadata
from homemaster.skills.commands import resolve_skill_command
from homemaster.web._shared import (
    _SKILL_SOURCE_LABELS,
    _application_services,
    _error,
)
from homemaster.web.context import WebAppContext
from homemaster.web.schemas import SkillImportRequest, SkillResolveRequest

logger = logging.getLogger(__name__)

_IMPORT_TOTAL_LIMIT = 4 * 1024 * 1024
_SKILL_DIR_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


def _user_skill_dirs(ctx: WebAppContext) -> tuple[Path, ...]:
    sources = getattr(ctx.config, "skills", None) if ctx.config is not None else None
    user_dirs = tuple(getattr(sources, "user_dirs", None) or ())
    if not user_dirs:
        user_dirs = _default_skill_user_dirs()
    return tuple(Path(directory).expanduser() for directory in user_dirs)


def _registry_error() -> Response:
    return _error(
        503,
        "skill_registry_unavailable",
        "The skill registry is unavailable.",
        retryable=True,
    )


def _is_deletable_skill(registry: object, skill: object, user_roots: list[Path]) -> bool:
    """Single predicate shared by the list ``deletable`` flag and DELETE."""

    base_dir_raw = getattr(skill, "base_dir", None)
    provenance = registry.provenance_for(skill)
    if base_dir_raw is None or not provenance or provenance[-1].source != "user":
        return False
    base_dir = Path(base_dir_raw).resolve()
    return any(base_dir.is_relative_to(root) for root in user_roots)


def register(app: FastAPI, ctx: WebAppContext) -> None:
    application = ctx.application

    @app.get("/api/skills")
    async def list_skills() -> object:
        registry = _application_services(application).get("skill_registry")
        if registry is None:
            return _error(
                503,
                "skill_registry_unavailable",
                "The skill registry is unavailable.",
                retryable=True,
            )
        try:
            await asyncio.to_thread(registry.refresh)
        except Exception:
            logger.exception("skill_registry_refresh_failed")
            return _error(
                503,
                "skill_registry_unavailable",
                "The skill registry could not be refreshed.",
                retryable=True,
            )
        skills: list[dict[str, object]] = []
        user_roots = [directory.resolve() for directory in _user_skill_dirs(ctx)]
        for skill in registry.list_skills():
            provenance = registry.provenance_for(skill)
            raw_source = provenance[-1].source if provenance else getattr(skill, "source", "")
            skills.append(
                {
                    "name": skill.name,
                    "command_name": skill.command_name,
                    "description": skill.description,
                    "source": _SKILL_SOURCE_LABELS.get(raw_source, raw_source),
                    "user_invocable": bool(skill.user_invocable),
                    # Per-skill enablement is a P1 feature (skills.enabled.json);
                    # every registered skill is invocable today.
                    "enabled": True,
                    # Same predicate DELETE enforces — the flag must not lie.
                    "deletable": _is_deletable_skill(registry, skill, user_roots),
                }
            )
        return {
            "skills": skills,
            "issues": [
                {"source": issue.source, "code": issue.code, "detail": issue.detail, "path": issue.path}
                for issue in registry.issues
            ],
        }

    @app.post("/api/skills/resolve")
    async def resolve_skill(body: SkillResolveRequest) -> object:
        registry = _application_services(application).get("skill_registry")
        if registry is None:
            return _error(
                503,
                "skill_registry_unavailable",
                "The skill registry is unavailable.",
                retryable=True,
            )
        try:
            resolved = resolve_skill_command(body.text, registry)
        except ValueError:
            return _error(
                422,
                "skill_not_invocable",
                "The skill cannot be invoked directly by users.",
                retryable=False,
            )
        if resolved is None:
            return {"kind": "plain"}
        name, _, arguments = body.text[1:].partition(" ")
        return {
            "kind": "skill",
            "name": name.strip(),
            "arguments": arguments.strip(),
            "prompt": resolved.prompt,
            "model_override": resolved.model_override,
        }

    @app.post("/api/skills/import")
    async def import_skill(body: SkillImportRequest) -> object:
        registry = _application_services(application).get("skill_registry")
        if registry is None:
            return _registry_error()
        if sum(len(item.content.encode("utf-8")) for item in body.files) > _IMPORT_TOTAL_LIMIT:
            return _error(
                413,
                "payload_too_large",
                "The skill payload exceeds 4 MiB.",
                retryable=False,
            )

        entries: list[tuple[PurePosixPath, str]] = []
        for item in body.files:
            rel = PurePosixPath(item.path)
            if rel.is_absolute() or ".." in rel.parts or len(rel.parts) < 2:
                return _error(
                    422,
                    "invalid_skill_path",
                    "Skill files must live inside the skill folder.",
                    retryable=False,
                )
            entries.append((rel, item.content))
        tops = {rel.parts[0] for rel, _content in entries}
        if len(tops) != 1:
            return _error(
                422,
                "invalid_skill_path",
                "All files must share one top-level skill directory.",
                retryable=False,
            )
        (top,) = tops
        if _SKILL_DIR_NAME.fullmatch(top) is None:
            return _error(
                422,
                "invalid_skill_path",
                "The skill directory name must be alphanumeric, '-' or '_'.",
                retryable=False,
            )

        skill_md = {rel.as_posix(): content for rel, content in entries}.get(f"{top}/SKILL.md")
        if skill_md is None:
            return _error(
                422,
                "missing_skill_md",
                "The folder must contain SKILL.md at its root.",
                retryable=False,
            )
        try:
            parsed = parse_skill_metadata(top, skill_md, fallback_template="Skill: {name}")
        except ValueError as exc:
            return _error(
                422,
                "invalid_skill",
                f"SKILL.md could not be parsed: {exc}",
                retryable=False,
            )
        skill_name = str(parsed["name"])

        user_root = _user_skill_dirs(ctx)[0].resolve()
        dest_dir = (user_root / top).resolve()
        if dest_dir.parent != user_root:
            return _error(
                422,
                "invalid_skill_path",
                "The skill directory must stay inside the user skill root.",
                retryable=False,
            )

        # Shadow check: a name/command conflict with a skill that does not live
        # in dest_dir would either fail registration or silently shadow a
        # builtin — reject before writing so the reply is never a fake success.
        for candidate in {skill_name, top}:
            conflict = registry.get(candidate)
            if conflict is None:
                continue
            provenance = registry.provenance_for(conflict)
            conflict_base = (
                Path(conflict.base_dir).resolve() if conflict.base_dir is not None else None
            )
            same_target = (
                provenance
                and provenance[-1].source == "user"
                and conflict_base == dest_dir
            )
            if not same_target:
                return _error(
                    409,
                    "skill_conflict",
                    f"A skill named {candidate!r} already exists from another source.",
                    retryable=False,
                )
        if (dest_dir.exists() or dest_dir.is_symlink()) and not body.overwrite:
            return _error(
                409,
                "skill_conflict",
                f"The skill directory {top!r} already exists.",
                retryable=False,
            )
        if dest_dir.is_symlink() or (dest_dir.exists() and not dest_dir.is_dir()):
            return _error(
                409,
                "skill_conflict",
                f"The target {top!r} is not a regular directory.",
                retryable=False,
            )

        staging_dir = dest_dir.with_name(f".{top}.import-staging")

        def _write() -> None:
            # Stage first so a mid-write failure cannot destroy the old copy;
            # the existing directory is only removed once staging is complete.
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
            staging_dir.mkdir(parents=True)
            try:
                for rel, content in entries:
                    target = staging_dir.joinpath(*rel.parts[1:]).resolve()
                    target.relative_to(staging_dir)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                if dest_dir.exists():
                    shutil.rmtree(dest_dir)
                staging_dir.rename(dest_dir)
            except Exception:
                shutil.rmtree(staging_dir, ignore_errors=True)
                raise

        try:
            await asyncio.to_thread(_write)
        except OSError:
            logger.exception("skill_import_write_failed")
            return _error(
                500,
                "skill_import_io",
                "The skill could not be written to the user skill directory.",
                retryable=True,
            )
        try:
            await asyncio.to_thread(registry.refresh)
        except Exception:
            logger.exception("skill_registry_refresh_failed")
            return _error(
                503,
                "skill_registry_unavailable",
                "The skill was written but the registry could not be refreshed.",
                retryable=True,
            )

        imported = registry.get(skill_name) or registry.get(top)
        provenance = registry.provenance_for(imported) if imported is not None else ()
        imported_base = (
            Path(imported.base_dir).resolve()
            if imported is not None and imported.base_dir is not None
            else None
        )
        if (
            imported is None
            or not provenance
            or provenance[-1].source != "user"
            or imported_base != dest_dir
        ):
            issue = next(
                (
                    item
                    for item in registry.issues
                    if item.path is not None and str(dest_dir) in item.path
                ),
                None,
            )
            detail = issue.detail if issue is not None else "the skill did not register"
            return _error(
                422,
                "skill_import_failed",
                f"The skill was written but failed to load: {detail}",
                retryable=False,
            )
        return {
            "status": "imported",
            "skill": {
                "name": imported.name,
                "command_name": imported.command_name,
                "description": imported.description,
                "source": "user",
                "user_invocable": bool(imported.user_invocable),
                "enabled": True,
                "deletable": True,
            },
        }

    @app.delete("/api/skills/{name}")
    async def delete_skill(name: str) -> object:
        registry = _application_services(application).get("skill_registry")
        if registry is None:
            return _registry_error()
        skill = registry.get(name)
        if skill is None:
            return _error(
                404,
                "skill_not_found",
                "The skill does not exist.",
                retryable=False,
            )
        user_roots = [directory.resolve() for directory in _user_skill_dirs(ctx)]
        if not _is_deletable_skill(registry, skill, user_roots):
            return _error(
                403,
                "skill_protected",
                "Only skills under the user skill directory can be deleted.",
                retryable=False,
            )
        base_dir = Path(skill.base_dir).resolve()
        try:
            await asyncio.to_thread(shutil.rmtree, base_dir)
        except OSError:
            logger.exception("skill_delete_failed")
            return _error(
                500,
                "skill_delete_io",
                "The skill directory could not be removed.",
                retryable=True,
            )
        try:
            await asyncio.to_thread(registry.refresh)
        except Exception:
            logger.exception("skill_registry_refresh_failed")
            return _error(
                503,
                "skill_registry_unavailable",
                "The skill was deleted but the registry could not be refreshed.",
                retryable=True,
            )
        # A shadowed copy in another user skill root resurrects on refresh —
        # tell the caller instead of silently reappearing.
        resurrected = registry.get(name)
        if resurrected is not None:
            return {
                "deleted": True,
                "name": name,
                "resurrected": True,
                "message": (
                    "Another skill with the same name still loads from a "
                    "different user skill directory."
                ),
            }
        return Response(status_code=204)


__all__ = ["register"]
