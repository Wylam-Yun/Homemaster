from __future__ import annotations

from pathlib import Path

import pytest

from homemaster.adapters.profiles import EnvironmentToolProfile
from homemaster.application.factory import create_application
from homemaster.application.session import SessionManager
from homemaster.config import HomeMasterConfig
from homemaster.events.bus import EventBus
from homemaster.observations import ObservationService
from homemaster.tools.catalog import ToolCatalog
from homemaster.tools.contracts import (
    RegisteredTool,
    ToolDefinition,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
)
from homemaster.tools.pipeline import ToolExecutionPipeline


class _Executor:
    async def execute(self, arguments, context) -> ToolExecutionResult:
        del arguments, context
        return ToolExecutionResult(status=ToolExecutionStatus.SUCCESS)


def _catalog_and_profile() -> tuple[ToolCatalog, EnvironmentToolProfile]:
    catalog = ToolCatalog()
    tool = RegisteredTool(
        definition=ToolDefinition(
            internal_id="test.echo.v1",
            model_alias="echo",
            description="Echo.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            verification_policy=VerificationPolicy(),
            provenance=ToolProvenance(source="test", reference="factory"),
            version="1.9.0",
        ),
        executor=_Executor(),
    )
    catalog.register(tool)
    return catalog, EnvironmentToolProfile(
        "test",
        catalog,
        catalog.freeze((tool.definition.internal_id,)),
    )


def test_factory_composes_injected_application_services_without_connecting(tmp_path) -> None:
    catalog, profile = _catalog_and_profile()
    service = ObservationService()
    bus = EventBus()
    sessions = SessionManager(session_root=tmp_path)
    pipeline = ToolExecutionPipeline(
        catalog,
        observation_service=service,
        public_event_sink=bus,
    )
    provider_calls = 0

    def provider_factory(request, run_id):
        nonlocal provider_calls
        del request, run_id
        provider_calls += 1
        return object()

    def context_factory(request, provider):
        del request, provider
        return None

    application = create_application(
        config=HomeMasterConfig(),
        profiles={"test": profile},
        catalog=catalog,
        pipeline=pipeline,
        observation_service=service,
        event_bus=bus,
        session_manager=sessions,
        provider_factory=provider_factory,
        context_assembler_factory=context_factory,
    )

    assert application.catalog is catalog
    assert application.pipeline is pipeline
    assert application.observation_service is service
    assert application.event_bus is bus
    assert application.session_manager is sessions
    assert application.profiles == {"test": profile}
    assert provider_calls == 0


def test_factory_rejects_empty_profiles() -> None:
    try:
        create_application(config=HomeMasterConfig(), profiles={})
    except ValueError as exc:
        assert str(exc) == "application requires at least one tool profile"
    else:
        raise AssertionError("empty profile mapping must be rejected")


def test_factory_rejects_profile_catalog_mismatch() -> None:
    catalog, profile = _catalog_and_profile()
    other_catalog = ToolCatalog()

    try:
        create_application(
            config=HomeMasterConfig(),
            profiles={"test": profile},
            catalog=other_catalog,
        )
    except ValueError as exc:
        assert str(exc) == "profiles must use the supplied application ToolCatalog"
    else:
        raise AssertionError("profile/catalog mismatch must be rejected")


@pytest.mark.asyncio
async def test_default_factory_builds_all_profiles_without_external_connections(
    tmp_path,
) -> None:
    config_path = Path(__file__).parents[3] / "config" / "homemaster.example.yaml"

    application = create_application(
        config_path=config_path,
        session_manager=SessionManager(session_root=tmp_path),
    )

    assert tuple(application.profiles) == ("home", "alfworld", "coworker")
    assert len(application.catalog.list_tools()) > 0
    await application.aclose()
