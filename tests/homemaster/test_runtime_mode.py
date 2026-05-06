"""Tests for P2: RuntimeMode, ComponentMode, boundary compat, provider labels."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from homemaster.stage_runtime import (
    KeywordEmbeddingProvider,
    RuntimeMode,
    ServiceCheckResult,
    StaticMemoryQueryProvider,
    StaticScenarioDecisionProvider,
    model_boundary,
    validate_runtime_services,
)


# ---------------------------------------------------------------------------
# RuntimeMode.from_flags
# ---------------------------------------------------------------------------


class TestRuntimeModeFromFlags:
    def test_from_flags_live(self) -> None:
        rm = RuntimeMode.from_flags(live_models=True, mock_skills=True)
        assert rm.task_understanding == "live_llm"
        assert rm.memory_query == "live_llm"
        assert rm.embedding == "live_embedding"
        assert rm.planning == "live_llm"
        assert rm.step_decision == "test_double"  # always test_double
        assert rm.step_decision_smoke == "live_llm"
        assert rm.skills == "mock_skill"
        assert rm.verification == "mock_symbolic"
        assert rm.summary == "live_llm"
        assert rm.memory_commit == "programmatic"
        assert rm.real_robot == "not_integrated"
        assert rm.real_vla == "not_integrated"
        assert rm.real_vlm == "not_integrated"

    def test_from_flags_deterministic(self) -> None:
        rm = RuntimeMode.from_flags(live_models=False, mock_skills=True)
        assert rm.task_understanding == "test_double"
        assert rm.memory_query == "test_double"
        assert rm.embedding == "test_double"
        assert rm.planning == "test_double"
        assert rm.step_decision == "test_double"
        assert rm.step_decision_smoke == "n/a"
        assert rm.skills == "mock_skill"
        assert rm.verification == "mock_symbolic"
        assert rm.summary == "test_double"
        assert rm.memory_commit == "programmatic"

    def test_from_flags_no_mock(self) -> None:
        rm = RuntimeMode.from_flags(live_models=False, mock_skills=False)
        assert rm.skills == "test_double"
        assert rm.task_understanding == "test_double"


# ---------------------------------------------------------------------------
# to_boundary_dict backward compat
# ---------------------------------------------------------------------------


class TestBoundaryCompat:
    def test_to_boundary_compat_live(self) -> None:
        rm = RuntimeMode.from_flags(live_models=True, mock_skills=True)
        boundary = rm.to_boundary_dict()
        assert boundary == {
            "stage02": "real_mimo",
            "stage03_query": "real_mimo",
            "stage03_embedding": "real_bge_m3",
            "stage04": "programmatic",
            "stage05_plan": "real_mimo",
            "stage05_step": "deterministic",  # honest: StaticScenarioDecisionProvider
            "stage05_navigation": "mock",
            "stage05_operation": "mock",
            "stage05_verification": "mock",  # mock_symbolic -> "mock"
            "stage06_summary": "real_mimo",
            "stage06_memory_commit": "programmatic",
            "real_robot": "not_integrated",
            "real_vla": "not_integrated",
            "real_vlm": "not_integrated",
        }

    def test_to_boundary_compat_deterministic(self) -> None:
        rm = RuntimeMode.from_flags(live_models=False, mock_skills=True)
        boundary = rm.to_boundary_dict()
        assert boundary == {
            "stage02": "deterministic",
            "stage03_query": "deterministic",
            "stage03_embedding": "deterministic",
            "stage04": "programmatic",
            "stage05_plan": "deterministic",
            "stage05_step": "deterministic",
            "stage05_navigation": "mock",
            "stage05_operation": "mock",
            "stage05_verification": "mock",
            "stage06_summary": "deterministic",
            "stage06_memory_commit": "programmatic",
            "real_robot": "not_integrated",
            "real_vla": "not_integrated",
            "real_vlm": "not_integrated",
        }

    def test_model_boundary_delegates(self) -> None:
        for live in (True, False):
            for mock in (True, False):
                rm = RuntimeMode.from_flags(live_models=live, mock_skills=mock)
                assert model_boundary(live_models=live, mock_skills=mock) == rm.to_boundary_dict()


# ---------------------------------------------------------------------------
# Provider test-double labels
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
        rm = RuntimeMode.from_flags(live_models=False, mock_skills=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            rm.task_understanding = "live_llm"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# validate_runtime_services
# ---------------------------------------------------------------------------


class TestValidateRuntimeServices:
    def test_missing_config(self) -> None:
        rm = RuntimeMode.from_flags(live_models=True, mock_skills=True)
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

    def test_test_double_no_check(self) -> None:
        rm = RuntimeMode.from_flags(live_models=False, mock_skills=True)
        checks = validate_runtime_services(
            rm,
            config_path="/nonexistent/config.json",
            provider_name="Mimo",
            embedding_provider_name="BGE",
        )
        assert checks == []
