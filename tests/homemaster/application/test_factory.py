from __future__ import annotations

import json
import subprocess
import sys

import pytest

from homemaster.adapters.profiles import EnvironmentToolProfile
from homemaster.agent.context import ContextAssembler
from homemaster.application.contracts import RunRequest, RunStatus
from homemaster.application.factory import (
    _provider_factory,
    _resolve_chat_provider,
    create_application,
)
from homemaster.application.resources import ApplicationResourceManager
from homemaster.application.session import SessionManager
from homemaster.config import (
    ContextPolicyConfig,
    HomeMasterConfig,
    ProviderProfileConfig,
)
from homemaster.devices import DeviceConnectionPool, DeviceLeaseError
from homemaster.events.bus import EventBus
from homemaster.permissions import HomePermissionPolicy
from homemaster.providers.transports.types import TransportDelta
from homemaster.tools.catalog import ToolCatalog
from homemaster.tools.contracts import (
    PermissionSubject,
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


class _TextTransport:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def stream(self, *args, **kwargs):
        del args, kwargs
        self.calls += 1
        yield TransportDelta(type="text", text_delta=self.text, finish_reason="stop")


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


def _model_override_config() -> HomeMasterConfig:
    return HomeMasterConfig.model_validate(
        {
            "providers": {
                "default": "primary",
                "items": [
                    {
                        "name": "primary",
                        "api_format": "openai",
                        "base_url": "https://primary.example.test/v1",
                        "model": "model-a",
                    },
                    {
                        "name": "secondary",
                        "api_format": "openai",
                        "base_url": "https://secondary.example.test/v1",
                        "model": "shared-model",
                    },
                    {
                        "name": "tertiary",
                        "api_format": "openai",
                        "base_url": "https://tertiary.example.test/v1",
                        "model": "shared-model",
                    },
                ],
            }
        }
    )


@pytest.mark.parametrize(
    ("override", "expected_name"),
    (("MODEL-A", "primary"), ("PRIMARY", "primary")),
)
def test_skill_model_override_resolves_unique_model_or_provider_name(
    override: str,
    expected_name: str,
) -> None:
    profile = _resolve_chat_provider(
        _model_override_config(),
        RunRequest(text="run skill", model_override=override),
    )

    assert profile.name == expected_name


@pytest.mark.parametrize("override", ("missing-model", "shared-model"))
def test_skill_model_override_rejects_zero_or_ambiguous_matches(override: str) -> None:
    with pytest.raises(ValueError, match="must map to exactly one"):
        _resolve_chat_provider(
            _model_override_config(),
            RunRequest(text="run skill", model_override=override),
        )


def test_skill_model_override_rejects_explicit_provider_conflict() -> None:
    with pytest.raises(ValueError, match="conflicts with the explicitly selected provider"):
        _resolve_chat_provider(
            _model_override_config(),
            RunRequest(
                text="run skill",
                provider_name="secondary",
                model_override="model-a",
            ),
        )


def test_skill_model_rejection_precedes_provider_client_construction(monkeypatch) -> None:
    constructor_calls = 0

    def forbidden_client(*args, **kwargs):
        nonlocal constructor_calls
        del args, kwargs
        constructor_calls += 1
        raise AssertionError("provider client must not be constructed")

    monkeypatch.setattr("homemaster.application.factory.LLMClient", forbidden_client)
    build = _provider_factory(_model_override_config())

    with pytest.raises(ValueError, match="must map to exactly one"):
        build(RunRequest(text="run skill", model_override="shared-model"), "run-1")

    assert constructor_calls == 0


def test_factory_composes_injected_application_services_without_connecting(tmp_path) -> None:
    catalog, profile = _catalog_and_profile()
    bus = EventBus()
    sessions = SessionManager(session_root=tmp_path)
    pipeline = ToolExecutionPipeline(catalog, public_event_sink=bus)
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
        event_bus=bus,
        session_manager=sessions,
        provider_factory=provider_factory,
        context_assembler_factory=context_factory,
    )

    assert application.catalog is catalog
    assert application.pipeline is pipeline
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


def test_factory_default_pipeline_uses_application_resource_manager() -> None:
    catalog, profile = _catalog_and_profile()

    application = create_application(
        config=HomeMasterConfig(),
        profiles={"test": profile},
        catalog=catalog,
    )

    assert isinstance(application.pipeline.resource_manager, ApplicationResourceManager)
    assert isinstance(application.pipeline.permission_policy, HomePermissionPolicy)
    connections = application.settings.device_connection_pool
    assert isinstance(connections, DeviceConnectionPool)
    assert connections.lease_manager is application.pipeline.resource_manager
    assert application.resource_scope.get("device-connection-pool").resource is connections


def test_factory_wires_configured_sensitive_values_into_runtime_settings() -> None:
    catalog, profile = _catalog_and_profile()
    config = HomeMasterConfig.model_validate(
        {
            "providers": {
                "default": "fake",
                "items": [
                    {
                        "name": "fake",
                        "api_format": "openai",
                        "base_url": "https://provider.example.test/v1",
                        "model": "fake-model",
                        "api_keys": ["provider-secret"],
                    }
                ],
            },
            "mcp": {
                "servers": {
                    "remote": {
                        "transport": "http",
                        "url": "https://example.test/mcp",
                        "headers": {"Authorization": "Bearer mcp-secret"},
                    }
                }
            },
        }
    )

    application = create_application(
        config=config,
        profiles={"test": profile},
        catalog=catalog,
    )

    assert set(application.settings.public_sensitive_values) == {
        "provider-secret",
        "Bearer mcp-secret",
    }


@pytest.mark.asyncio
async def test_factory_owns_and_closes_default_device_connection_pool() -> None:
    catalog, profile = _catalog_and_profile()
    application = create_application(
        config=HomeMasterConfig(),
        profiles={"test": profile},
        catalog=catalog,
    )
    connections = application.settings.device_connection_pool

    assert connections.closed is False
    await application.aclose()
    assert connections.closed is True


@pytest.mark.asyncio
async def test_factory_runtime_pins_borrowed_backend_to_first_tenant(tmp_path) -> None:
    catalog, profile = _catalog_and_profile()
    transports = {
        "first": _TextTransport("first done"),
        "second": _TextTransport("second done"),
    }
    provider_profile = ProviderProfileConfig(
        name="fake",
        api_format="openai",
        base_url="https://provider.example.test/v1",
        model="fake-model",
    )

    def provider_factory(request, run_id):
        del run_id
        return transports[request.text]

    def context_factory(request, provider):
        del request
        return ContextAssembler(
            provider=provider_profile,
            policy=ContextPolicyConfig(),
            system_prompt="system",
            summary_client=provider,
        )

    application = create_application(
        config=HomeMasterConfig.model_validate(
            {"observability": {"session_dir": str(tmp_path / "sessions")}}
        ),
        profiles={"test": profile},
        catalog=catalog,
        provider_factory=provider_factory,
        context_assembler_factory=context_factory,
    )

    class Backend:
        backend_id = "physical-backend"
        device_id = "device"
        generation = 0

        def __init__(self) -> None:
            self.close_count = 0

        def close(self) -> None:
            self.close_count += 1

    backend = Backend()

    def request(text: str, tenant_id: str) -> RunRequest:
        return RunRequest(
            text=text,
            profile="test",
            environment=backend,
            permission_subject=PermissionSubject(
                subject_id=f"operator-{tenant_id}",
                tenant_id=tenant_id,
                channel="gateway",
                capabilities=("device.control",),
            ),
        )

    first = await application.run(request("first", "tenant-a"))
    assert first.status is RunStatus.REPLIED
    assert transports["first"].calls == 1

    with pytest.raises(DeviceLeaseError) as error:
        await application.run(request("second", "tenant-b"))
    assert error.value.error_code == "cross_tenant_device"
    assert transports["second"].calls == 0

    await application.aclose()
    assert backend.close_count == 0


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


def test_factory_requires_profiles_from_outer_composition_root() -> None:
    with pytest.raises(ValueError, match="profiles must be composed and supplied"):
        create_application(config=HomeMasterConfig())


def test_importing_application_factory_does_not_load_benchmark_modules() -> None:
    command = (
        "import json, sys; import homemaster.application.factory; "
        "print(json.dumps(sorted(name for name in sys.modules "
        "if name.startswith('homemaster.benchmarking'))))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", command],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == []
