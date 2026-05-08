"""Tests for StageRegistry.get_stage() added in P9."""

from __future__ import annotations

import pytest

from homemaster.pipeline.core import PipelineContext, StageRegistry


class _StubStage:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        return ctx


def test_get_stage_returns_correct_stage() -> None:
    registry = StageRegistry()
    stage_a = _StubStage("stage_a")
    stage_b = _StubStage("stage_b")
    registry.register(stage_a)
    registry.register(stage_b)

    assert registry.get_stage("stage_a") is stage_a
    assert registry.get_stage("stage_b") is stage_b


def test_get_stage_raises_on_missing() -> None:
    registry = StageRegistry()
    registry.register(_StubStage("stage_a"))

    with pytest.raises(KeyError, match="stage 'nonexistent' not registered"):
        registry.get_stage("nonexistent")
