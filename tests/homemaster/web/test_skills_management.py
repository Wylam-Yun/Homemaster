"""Skill management routes: POST /api/skills/import + DELETE /api/skills/{name}."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from homemaster.memory.mindmemos_client import MindMemOSClientError

from homemaster.skills.loader import load_skill_registry
from homemaster.skills.registry import SkillProvenance, SkillRegistry
from homemaster.skills.types import SkillDefinition
from homemaster.web.app import create_web_app
from homemaster.web.confirmations import WebConfirmationHandler
from tests.homemaster.web.test_p03_batch import _P03Application

_SKILL_MD = """---
name: housekeeping
description: tidy the room
---

Do the housekeeping.
"""


def _registry_with(user_root: Path, *extra: SkillDefinition) -> SkillRegistry:
    """A real registry whose refresher rediscovers ``user_root`` each refresh."""

    def discover() -> SkillRegistry:
        registry = load_skill_registry(
            cwd=None,  # project discovery off; tmp dir may not exist yet
            user_dirs=(user_root,),
        )
        for skill in extra:
            registry.register(
                skill,
                provenance=SkillProvenance(
                    source=skill.source, path=Path("/x"), root=Path("/x")
                ),
            )
        return registry

    registry = discover()
    registry.set_refresher(discover)
    return registry


def _app_for(user_root: Path, registry: SkillRegistry):
    application = _P03Application(services={"skill_registry": registry})
    config = SimpleNamespace(skills=SimpleNamespace(user_dirs=(user_root,)))
    return create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
        config=config,
    )


def _app_for_roots(user_roots: tuple[Path, ...], registry: SkillRegistry):
    application = _P03Application(services={"skill_registry": registry})
    config = SimpleNamespace(skills=SimpleNamespace(user_dirs=user_roots))
    return create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
        config=config,
    )


def _import_payload(top: str = "housekeeping", **overrides) -> dict:
    payload = {
        "files": [
            {"path": f"{top}/SKILL.md", "content": _SKILL_MD},
            {"path": f"{top}/notes.md", "content": "extra notes"},
        ]
    }
    payload.update(overrides)
    return payload


def test_import_writes_files_registers_and_reports(tmp_path) -> None:
    user_root = tmp_path / "skills"
    app = _app_for(user_root, _registry_with(user_root))

    with TestClient(app) as client:
        response = client.post("/api/skills/import", json=_import_payload())

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "imported"
        assert body["skill"]["name"] == "housekeeping"
        assert body["skill"]["deletable"] is True
        # External terminal state: files really landed in the user skill root.
        assert (user_root / "housekeeping" / "SKILL.md").read_text() == _SKILL_MD
        assert (user_root / "housekeeping" / "notes.md").is_file()
        # The same registry now lists it as a user-source skill.
        listed = client.get("/api/skills").json()["skills"]
        entry = next(item for item in listed if item["name"] == "housekeeping")
        assert entry["source"] == "user"


def test_import_rejects_missing_skill_md(tmp_path) -> None:
    user_root = tmp_path / "skills"
    app = _app_for(user_root, _registry_with(user_root))

    with TestClient(app) as client:
        response = client.post(
            "/api/skills/import",
            json={"files": [{"path": "housekeeping/notes.md", "content": "x"}]},
        )

    assert response.status_code == 422
    assert response.json()["code"] == "missing_skill_md"
    assert not (user_root / "housekeeping").exists()


def test_import_rejects_traversal_and_absolute_paths(tmp_path) -> None:
    user_root = tmp_path / "skills"
    app = _app_for(user_root, _registry_with(user_root))

    with TestClient(app) as client:
        for bad_path in ("housekeeping/../escape.md", "/abs/SKILL.md", "../SKILL.md"):
            response = client.post(
                "/api/skills/import",
                json={
                    "files": [
                        {"path": bad_path, "content": "x"},
                        {"path": "housekeeping/SKILL.md", "content": _SKILL_MD},
                    ]
                },
            )
            assert response.status_code == 422, bad_path
            assert response.json()["code"] == "invalid_skill_path"
        assert not (tmp_path / "escape.md").exists()


def test_import_rejects_multiple_top_dirs_and_empty_files(tmp_path) -> None:
    user_root = tmp_path / "skills"
    app = _app_for(user_root, _registry_with(user_root))

    with TestClient(app) as client:
        response = client.post(
            "/api/skills/import",
            json={
                "files": [
                    {"path": "one/SKILL.md", "content": _SKILL_MD},
                    {"path": "two/SKILL.md", "content": _SKILL_MD},
                ]
            },
        )
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_skill_path"

        empty = client.post("/api/skills/import", json={"files": []})
        assert empty.status_code == 422


def test_import_rejects_oversized_payload(tmp_path) -> None:
    user_root = tmp_path / "skills"
    app = _app_for(user_root, _registry_with(user_root))

    with TestClient(app) as client:
        response = client.post(
            "/api/skills/import",
            json={
                "files": [
                    {"path": "housekeeping/SKILL.md", "content": _SKILL_MD},
                    *(
                        {"path": f"housekeeping/big-{index}.md", "content": "x" * 900_000}
                        for index in range(5)
                    ),
                ]
            },
        )

    # Each file is under the 1 MB per-file cap; the aggregate trips 4 MiB.
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


def test_import_conflict_and_deterministic_overwrite(tmp_path) -> None:
    user_root = tmp_path / "skills"
    stale = user_root / "housekeeping"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    (stale / "stale.txt").write_text("old junk", encoding="utf-8")
    app = _app_for(user_root, _registry_with(user_root))

    with TestClient(app) as client:
        conflict = client.post("/api/skills/import", json=_import_payload())
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "skill_conflict"

        ok = client.post("/api/skills/import", json=_import_payload(overwrite=True))
        assert ok.status_code == 200
        # Deterministic replacement: stale files are gone, not merged.
        assert not (stale / "stale.txt").exists()
        assert (stale / "notes.md").is_file()


def test_import_rejects_builtin_name_conflict(tmp_path) -> None:
    user_root = tmp_path / "skills"
    builtin = SkillDefinition(
        name="housekeeping",
        description="builtin",
        content="x",
        source="builtin",
        command_name="housekeeping",
    )
    app = _app_for(user_root, _registry_with(user_root, builtin))

    with TestClient(app) as client:
        response = client.post(
            "/api/skills/import", json=_import_payload(overwrite=True)
        )

    assert response.status_code == 409
    assert response.json()["code"] == "skill_conflict"
    assert not (user_root / "housekeeping").exists()


def test_delete_user_skill_removes_dir_and_registry_entry(tmp_path) -> None:
    user_root = tmp_path / "skills"
    target = user_root / "housekeeping"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    registry = _registry_with(user_root)
    app = _app_for(user_root, registry)

    with TestClient(app) as client:
        assert client.get("/api/skills").json()["skills"]
        response = client.delete("/api/skills/housekeeping")

        assert response.status_code == 204
        assert not target.exists()
        assert registry.get("housekeeping") is None


def test_delete_rejects_builtin_and_explicit_skills(tmp_path) -> None:
    user_root = tmp_path / "skills"
    builtin = SkillDefinition(
        name="core",
        description="builtin",
        content="x",
        source="builtin",
        command_name="core",
    )
    external_dir = tmp_path / "elsewhere"
    external_dir.mkdir()
    (external_dir / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    explicit = SkillDefinition(
        name="external",
        description="explicit",
        content="x",
        source="explicit",
        command_name="external",
        base_dir=str(external_dir),
    )
    app = _app_for(user_root, _registry_with(user_root, builtin, explicit))

    with TestClient(app) as client:
        for name in ("core", "external"):
            response = client.delete(f"/api/skills/{name}")
            assert response.status_code == 403, name
            assert response.json()["code"] == "skill_protected"
        assert (external_dir / "SKILL.md").exists()
        missing = client.delete("/api/skills/nope")
        assert missing.status_code == 404
        assert missing.json()["code"] == "skill_not_found"


def test_delete_reports_resurrection_when_shadowed_copy_remains(tmp_path) -> None:
    """Deleting the winning copy exposes a shadowed same-name skill → 200 note."""

    root_a = tmp_path / "skills-a"
    root_b = tmp_path / "skills-b"
    for root in (root_a, root_b):
        target = root / "housekeeping"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")

    def discover() -> SkillRegistry:
        return load_skill_registry(cwd=None, user_dirs=(root_a, root_b))

    registry = discover()
    registry.set_refresher(discover)
    app = _app_for_roots((root_a, root_b), registry)

    with TestClient(app) as client:
        response = client.delete("/api/skills/housekeeping")

        assert response.status_code == 200
        body = response.json()
        assert body["resurrected"] is True
        # Exactly one shadowed directory was removed; the other revives the name.
        remaining = [root for root in (root_a, root_b) if (root / "housekeeping").exists()]
        assert len(remaining) == 1
        assert registry.get("housekeeping") is not None


# ---------------------------------------------------------------------------
# Skill evolution observability routes
# ---------------------------------------------------------------------------


def _evolution_store() -> MagicMock:
    store = MagicMock()
    store.skills_with_invocations.return_value = [
        {"cloud_skill_id": "cs-1", "skill_id": "housekeeping", "invocation_count": 9}
    ]
    store.unconsumed_trace_count.return_value = 1
    store.skill_evolution_batches_for_skill.return_value = [
        SimpleNamespace(
            batch_id="b-1",
            job_id="j-1",
            status="succeeded",
            created_at="2026-10-28T10:00:00Z",
            trace_ids=["t-1"],
        )
    ]
    store.job.return_value = SimpleNamespace(
        job_id="j-1",
        status="succeeded",
        attempts=1,
        last_error=None,
        result={"draft_version_id": "v2"},
    )
    store.skill_invocations_for_skill.return_value = [
        SimpleNamespace(
            invocation_id="inv-1",
            skill_id="housekeeping",
            status="consumed",
            score=0.9,
            outcome="success",
            task_input="tidy",
            sealed_at="2026-10-28T09:00:00Z",
            evolution_batch_id="b-1",
            skill_version_id="v1",
        )
    ]
    store.invocation_steps.return_value = [
        SimpleNamespace(
            tool_call_id="call-1",
            tool_name="load_skill",
            arguments={"name": "housekeeping"},
            status="succeeded",
        )
    ]
    return store


def _evolution_client() -> MagicMock:
    client = MagicMock()
    client.list_skill_versions = AsyncMock(
        return_value=[
            SimpleNamespace(
                version_id="v2",
                parent_version_id="v1",
                status="draft",
                version_label="evo-1",
                content_hash="h2",
            ),
            SimpleNamespace(
                version_id="v1",
                parent_version_id=None,
                status="observed",
                version_label=None,
                content_hash="h1",
            ),
        ]
    )
    client.get_skill_version_content = AsyncMock(
        side_effect=lambda *, cloud_skill_id, version_id: SimpleNamespace(
            version=SimpleNamespace(
                version_id=version_id,
                parent_version_id="v1" if version_id == "v2" else None,
                status="draft" if version_id == "v2" else "observed",
            ),
            content=json.dumps(
                [{"path": "SKILL.md", "content": f"---\nname: sk-{version_id}\n---\nbody-{version_id}"}]
            ),
        )
    )
    return client


def _evolution_app(store, client):
    application = _P03Application(
        services={"memory_work_store": store, "mindmemos_client": client}
    )
    config = SimpleNamespace(skills=SimpleNamespace(user_dirs=()))
    return create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
        config=config,
    )


def test_skill_evolution_overview_lists_skills() -> None:
    store = _evolution_store()
    with TestClient(_evolution_app(store, _evolution_client())) as client:
        response = client.get("/api/skill-evolution")
        assert response.status_code == 200
        [skill] = response.json()["skills"]
        assert skill["cloud_skill_id"] == "cs-1"
        assert skill["skill_id"] == "housekeeping"
        assert skill["invocation_count"] == 9
        assert skill["unconsumed_count"] == 1
        assert skill["batches_count"] == 1
        assert skill["last_draft_version_id"] == "v2"


def test_skill_evolution_detail_shapes_invocations_batches_versions() -> None:
    store = _evolution_store()
    remote = _evolution_client()
    with TestClient(_evolution_app(store, remote)) as client:
        response = client.get("/api/skill-evolution/cs-1")
        assert response.status_code == 200
        body = response.json()
        [invocation] = body["invocations"]
        assert invocation["invocation_id"] == "inv-1"
        assert invocation["evolution_batch_id"] == "b-1"
        [step] = invocation["steps"]
        assert step["tool_name"] == "load_skill"
        [batch] = body["batches"]
        assert batch["batch_id"] == "b-1"
        assert batch["draft_version_id"] == "v2"
        assert batch["job"]["status"] == "succeeded"
        assert body["versions"][0]["version_id"] == "v2"
        assert body["versions_unavailable"] is False


def test_skill_evolution_detail_marks_versions_unavailable() -> None:
    store = _evolution_store()
    remote = _evolution_client()
    remote.list_skill_versions.side_effect = MindMemOSClientError("down")
    with TestClient(_evolution_app(store, remote)) as client:
        response = client.get("/api/skill-evolution/cs-1")
        assert response.status_code == 200
        body = response.json()
        assert body["versions"] is None
        assert body["versions_unavailable"] is True


def test_skill_evolution_version_content_returns_draft_and_parent() -> None:
    store = _evolution_store()
    remote = _evolution_client()
    with TestClient(_evolution_app(store, remote)) as client:
        response = client.get("/api/skill-evolution/cs-1/versions/v2/content")
        assert response.status_code == 200
        body = response.json()
        assert body["version_id"] == "v2"
        assert body["content"].endswith("body-v2")
        assert body["parent"]["version_id"] == "v1"
        assert body["parent"]["content"].endswith("body-v1")


def test_skill_evolution_version_content_404_for_foreign_version() -> None:
    store = _evolution_store()
    remote = _evolution_client()
    with TestClient(_evolution_app(store, remote)) as client:
        response = client.get("/api/skill-evolution/cs-1/versions/v999/content")
        assert response.status_code == 404
        assert response.json()["code"] == "skill_version_not_found"
        remote.get_skill_version_content.assert_not_called()


def test_skill_evolution_unavailable_without_store() -> None:
    application = _P03Application(services={})
    config = SimpleNamespace(skills=SimpleNamespace(user_dirs=()))
    app = create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
        config=config,
    )
    with TestClient(app) as client:
        response = client.get("/api/skill-evolution")
        assert response.status_code == 503
        assert response.json()["code"] == "skill_evolution_unavailable"
