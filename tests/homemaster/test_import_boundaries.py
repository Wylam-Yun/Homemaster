"""Import boundary tests for HomeMaster.

Verifies:
- Agent core does not import home domain
- Generic tools do not import home domain
- Context composer has no home task fields
- Skills do not import runtime or CLI
- Domain tools do not import CLI or runtime loop
- Deleted legacy packages are absent
- Package entrypoints do not export deleted legacy packages
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_generic_agent_core_does_not_import_home_domain() -> None:
    for path in (ROOT / "src/homemaster/agent").glob("*.py"):
        if path.name in {"turn.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert "homemaster.domain.home" not in text
        assert "homemaster.memory." not in text


def test_generic_tools_do_not_import_home_domain() -> None:
    for path in (ROOT / "src/homemaster/tools").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "homemaster.domain.home" not in text


def test_agent_context_composer_has_no_home_task_fields() -> None:
    for rel in ("src/homemaster/agent/context.py", "src/homemaster/agent/context_builder.py"):
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert "task_card" not in text
        assert "target_candidates" not in text
        assert "current_location" not in text
        assert "holding_object" not in text
        assert "memory_hits" not in text


def test_skills_do_not_import_runtime_or_cli() -> None:
    for path in (ROOT / "src/homemaster/skills").rglob("*"):
        if path.suffix not in {".py", ".md"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert "homemaster.agent.runtime" not in text
        assert "homemaster.cli" not in text
        assert "homemaster.pipeline" not in text
        assert "homemaster.stages" not in text


def test_domain_tools_do_not_import_cli_or_runtime_loop() -> None:
    for path in (ROOT / "src/homemaster/domain/home").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "homemaster.cli" not in text
        assert "homemaster.agent.runtime" not in text


def test_deleted_legacy_packages_are_absent() -> None:
    assert not (ROOT / "src/homemaster/pipeline").exists()
    assert not (ROOT / "src/homemaster/stages").exists()
    assert not (ROOT / "src/homemaster/task_runner.py").exists()
    assert not (ROOT / "src/homemaster/agent/runtime.py").exists()
    assert not (ROOT / "src/homemaster/providers/mimo_decision_client.py").exists()
    assert not (ROOT / "src/homemaster/agent/context.py").exists()
    assert not (ROOT / "src/homemaster/agent/context_builder.py").exists()
    assert not (ROOT / "src/homemaster/tools/builtin.py").exists()
    assert not (ROOT / "src/homemaster/tools/skill_tools.py").exists()
    assert not (ROOT / "src/homemaster/tools/state_updater.py").exists()
    assert not (ROOT / "src/homemaster/tools/simulated.py").exists()
    assert not (ROOT / "src/homemaster/memory/context_snapshot.py").exists()
    assert not (ROOT / "src/homemaster/memory/commit.py").exists()
    assert not (ROOT / "src/homemaster/memory/profile.py").exists()
    assert not (ROOT / "src/homemaster/memory/fact_memory.py").exists()
    assert not (ROOT / "src/homemaster/memory/task_record.py").exists()
    assert not (ROOT / "src/homemaster/domain/home/execution_state.py").exists()
    assert not (ROOT / "src/homemaster/domain/home/failure_log.py").exists()
    assert not (ROOT / "src/homemaster/domain/home/failure_rule_provider.py").exists()
    assert not (ROOT / "src/homemaster/domain/home/orchestration_validator.py").exists()
    assert not (ROOT / "src/homemaster/domain/home/planning_context.py").exists()
    assert not (ROOT / "src/homemaster/domain/home/world_overlay.py").exists()
    assert not (ROOT / "tests/homemaster/test_doubles/fake_mimo_client.py").exists()


def test_package_entrypoints_do_not_export_deleted_legacy_packages() -> None:
    for rel in ("src/homemaster/__init__.py", "src/homemaster/agent/__init__.py"):
        text = read(rel)
        assert '"pipeline"' not in text
        assert '"stages"' not in text
        assert '"task_runner"' not in text
        assert '"decision"' not in text
