"""Tests for P2: RuntimeMode, ComponentMode, boundary compat, provider labels."""

from __future__ import annotations

import dataclasses

import pytest

from homemaster.pipeline.stage_runtime import (
    RuntimeMode,
    ServiceCheckResult,
    model_boundary,
    validate_runtime_services,
)
from homemaster.runtime import RuntimeConfigError
from tests.homemaster.test_doubles.runtime_providers import (
    KeywordEmbeddingProvider,
    StaticMemoryQueryProvider,
    StaticScenarioDecisionProvider,
)


# ---------------------------------------------------------------------------
# RuntimeMode.live
# ---------------------------------------------------------------------------


class TestRuntimeModeLive:
    def test_live(self) -> None:
        rm = RuntimeMode.live()
        assert rm.task_understanding == "live_llm"
        assert rm.memory_query == "live_llm"
        assert rm.embedding == "live_embedding"
        assert rm.planning == "live_llm"
        assert rm.step_decision == "live_llm"
        assert rm.skills == "simulated_skill"
        assert rm.verification == "simulated_verification"
        assert rm.summary == "live_llm"
        assert rm.memory_commit == "programmatic"
        assert rm.real_robot == "not_integrated"
        assert rm.real_vla == "not_integrated"
        assert rm.real_vlm == "not_integrated"

    def test_live_no_step_decision_smoke(self) -> None:
        rm = RuntimeMode.live()
        assert not hasattr(rm, "step_decision_smoke")

    def test_live_real_skill_mode_raises(self) -> None:
        from homemaster.runtime import RuntimeConfigError

        with pytest.raises(RuntimeConfigError, match="not integrated"):
            RuntimeMode.live(skill_mode="real")


# ---------------------------------------------------------------------------
# RuntimeMode.from_flags (legacy compat)
# ---------------------------------------------------------------------------


class TestRuntimeModeFromFlags:
    def test_from_flags_live_raises(self) -> None:
        with pytest.raises(RuntimeConfigError, match="have been removed"):
            RuntimeMode.from_flags(live_models=True, mock_skills=True)

    def test_from_flags_deterministic_raises(self) -> None:
        with pytest.raises(RuntimeConfigError, match="have been removed"):
            RuntimeMode.from_flags(live_models=False, mock_skills=True)


# ---------------------------------------------------------------------------
# to_boundary_dict backward compat
# ---------------------------------------------------------------------------


class TestBoundaryCompat:
    def test_to_boundary_compat_live(self) -> None:
        rm = RuntimeMode.live()
        boundary = rm.to_boundary_dict()
        assert boundary == {
            "stage02": "real_mimo",
            "stage03_query": "real_mimo",
            "stage03_embedding": "real_bge_m3",
            "stage04": "programmatic",
            "stage05_plan": "real_mimo",
            "stage05_step": "real_mimo",
            "stage05_navigation": "simulated",
            "stage05_operation": "simulated",
            "stage05_verification": "simulated",
            "stage06_summary": "real_mimo",
            "stage06_memory_commit": "programmatic",
            "real_robot": "not_integrated",
            "real_vla": "not_integrated",
            "real_vlm": "not_integrated",
        }

    def test_model_boundary_delegates(self) -> None:
        assert model_boundary() == RuntimeMode.live().to_boundary_dict()


# ---------------------------------------------------------------------------
# Provider test-double labels (from test_doubles/)
# ---------------------------------------------------------------------------


class TestProviderLabels:
    def test_static_memory_query_provider(self) -> None:
        assert StaticMemoryQueryProvider.runtime_mode == "test_double"

    def test_keyword_embedding_provider(self) -> None:
        assert KeywordEmbeddingProvider.runtime_mode == "test_double"

    def test_static_scenario_decision_provider(self) -> None:
        assert StaticScenarioDecisionProvider.runtime_mode == "test_double"


# ---------------------------------------------------------------------------
# RuntimeMode frozen
# ---------------------------------------------------------------------------


class TestRuntimeModeFrozen:
    def test_frozen(self) -> None:
        rm = RuntimeMode.live()
        with pytest.raises(dataclasses.FrozenInstanceError):
            rm.task_understanding = "live_llm"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# validate_runtime_services
# ---------------------------------------------------------------------------


class TestValidateRuntimeServices:
    def test_missing_config(self) -> None:
        rm = RuntimeMode.live()
        checks = validate_runtime_services(
            rm,
            config_path="/nonexistent/config.json",
            provider_name="Mimo",
            embedding_provider_name="BGE",
        )
        assert len(checks) == 2
        assert all(not c.available for c in checks)
        assert checks[0].component == "llm_provider"
        assert checks[1].component == "embedding_provider"
