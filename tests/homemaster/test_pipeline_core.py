"""Tests for pipeline_core: PipelineContext, StageRegistry, build_default_registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from homemaster.pipeline_core import PipelineContext, StageRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_ctx(**overrides: object) -> PipelineContext:
    """Build a minimal PipelineContext for unit testing."""
    defaults: dict[str, object] = dict(
        run_id="test-001",
        scenario="test_scenario",
        utterance="test utterance",
        resolved_world_path=Path("/dev/null"),
        resolved_memory_path=Path("/dev/null"),
        runtime_memory_dir=Path("/tmp/test_memory"),
        case_dir=Path("/tmp/test_case"),
        results_dir=Path("/tmp/test_results"),
        live_models=False,
        mock_skills=True,
        config_path=Path("/dev/null"),
        provider_name="test",
        embedding_provider_name="test",
    )
    defaults.update(overrides)
    return PipelineContext(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# PipelineContext
# ---------------------------------------------------------------------------


class TestPipelineContext:
    def test_minimal_construction(self) -> None:
        ctx = _minimal_ctx()
        assert ctx.run_id == "test-001"
        assert ctx.task_card is None
        assert ctx.stage_statuses == {}
        assert ctx.final_status == "failed"

    def test_with_updates_returns_new_instance(self) -> None:
        ctx = _minimal_ctx()
        ctx2 = ctx.with_updates(task_card="fake_card", final_status="completed")
        # original unchanged
        assert ctx.task_card is None
        assert ctx.final_status == "failed"
        # new instance has updates
        assert ctx2.task_card == "fake_card"
        assert ctx2.final_status == "completed"
        assert ctx2.run_id == ctx.run_id

    def test_with_stage_status_copy_on_write(self) -> None:
        ctx = _minimal_ctx()
        ctx2 = ctx.with_stage_status("stage02", {"status": "PASS"})
        # original unchanged
        assert ctx.stage_statuses == {}
        # new instance has the entry
        assert ctx2.stage_statuses == {"stage02": {"status": "PASS"}}

    def test_with_stage_status_accumulates(self) -> None:
        ctx = _minimal_ctx()
        ctx2 = ctx.with_stage_status("stage02", {"status": "PASS"})
        ctx3 = ctx2.with_stage_status("stage03", {"status": "PASS", "mode": "deterministic"})
        assert ctx3.stage_statuses == {
            "stage02": {"status": "PASS"},
            "stage03": {"status": "PASS", "mode": "deterministic"},
        }
        # ctx2 still only has stage02
        assert ctx2.stage_statuses == {"stage02": {"status": "PASS"}}

    def test_with_final_status_copy_on_write(self) -> None:
        ctx = _minimal_ctx()
        ctx2 = ctx.with_final_status("completed")
        assert ctx.final_status == "failed"
        assert ctx2.final_status == "completed"


# ---------------------------------------------------------------------------
# StageRegistry
# ---------------------------------------------------------------------------


class _DummyStage:
    def __init__(self, n: str) -> None:
        self._name = n

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: PipelineContext) -> PipelineContext:  # pragma: no cover
        return ctx


class TestStageRegistry:
    def test_register_and_retrieve_in_order(self) -> None:
        reg = StageRegistry()
        reg.register(_DummyStage("a"))
        reg.register(_DummyStage("b"))
        names = [s.name for s in reg.stages()]
        assert names == ["a", "b"]

    def test_duplicate_registration_raises(self) -> None:
        reg = StageRegistry()
        reg.register(_DummyStage("x"))
        with pytest.raises(ValueError, match="already registered"):
            reg.register(_DummyStage("x"))

    def test_has(self) -> None:
        reg = StageRegistry()
        assert not reg.has("x")
        reg.register(_DummyStage("x"))
        assert reg.has("x")

    def test_len(self) -> None:
        reg = StageRegistry()
        assert len(reg) == 0
        reg.register(_DummyStage("a"))
        assert len(reg) == 1


# ---------------------------------------------------------------------------
# build_default_registry
# ---------------------------------------------------------------------------


class TestBuildDefaultRegistry:
    def test_has_five_stages(self) -> None:
        from homemaster.pipeline_core import build_default_registry

        reg = build_default_registry()
        assert len(reg) == 5
        for name in ("stage02", "stage03", "stage04", "stage05", "stage06"):
            assert reg.has(name), f"missing {name}"
