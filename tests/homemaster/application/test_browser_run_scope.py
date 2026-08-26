from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from homemaster.agent.context import ContextAssembler
from homemaster.agent.messages import ToolCall
from homemaster.application import ApplicationRuntime, RunRequest, RunStatus
from homemaster.application.session import SessionManager
from homemaster.browser.contracts import BrowserSnapshot
from homemaster.config import ContextPolicyConfig, ProviderProfileConfig
from homemaster.events.bus import EventBus
from homemaster.providers.attempts import ProviderAttemptRecord
from homemaster.providers.transports.types import TransportDelta
from homemaster.tools.adapters import from_registered_tool
from homemaster.tools.base import ToolRegistry
from homemaster.tools.contracts import (
    RegisteredTool,
    ToolDefinition,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
)
from homemaster.tools.executor import ToolExecutor
from homemaster.tools.observe import ScreenshotTool


class _Transport:
    def __init__(self, *, call_browser: bool = False) -> None:
        self.tools: list[dict[str, object]] | None = None
        self._call_browser = call_browser
        self._calls = 0

    async def stream(
        self, messages, *, tools=None, attempt_sink=None, model_attempt_id="a", **kwargs
    ):
        del messages, kwargs
        self.tools = list(tools or [])
        self._calls += 1
        if attempt_sink is not None:
            attempt_sink.record_attempt(
                ProviderAttemptRecord(
                    model_attempt_id=model_attempt_id,
                    request_sha256=hashlib.sha256(repr(tools).encode()).hexdigest(),
                    outbound_images=(),
                    stripped_images=False,
                    response_completed=True,
                    error_type=None,
                    cause_code=None,
                )
            )
        if self._call_browser and self._calls == 1:
            yield TransportDelta(
                type="tool_call",
                tool_call_delta=ToolCall(id="inspect-1", name="browser_inspect", arguments={}),
                finish_reason="tool_calls",
            )
        else:
            yield TransportDelta(type="text", text_delta="done", finish_reason="stop")


class _BlockingTransport(_Transport):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(self, *args, **kwargs):
        self.entered.set()
        await self.release.wait()
        async for delta in super().stream(*args, **kwargs):
            yield delta


class _Session:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.closed = False
        self.inspect_calls = 0

    async def navigate(self, url, **kwargs):
        del kwargs
        return {"url": url}

    async def history(self, action, **kwargs):
        del action, kwargs
        return {}

    async def inspect(self, filters):
        del filters
        self.inspect_calls += 1
        return BrowserSnapshot("s1", 0, "about:blank", "", "", (), 0, False)

    async def find(self, query):
        del query
        return {}

    async def read(self, query):
        del query
        return {}

    async def extract(self, query):
        del query
        return {}

    async def fill(self, target, value):
        del target
        return {"actual": value}

    async def type(self, target, text, **kwargs):
        del target, kwargs
        return {"actual": text}

    async def select(self, target, option, **kwargs):
        del target, kwargs
        return {"actual": option}

    async def check(self, target):
        del target
        return {"actual": True}

    async def uncheck(self, target):
        del target
        return {"actual": False}

    async def click(self, target, **kwargs):
        del target, kwargs
        return {"interaction_verified": True}

    async def hover(self, target, **kwargs):
        del target, kwargs
        return {"hovered": True}

    async def focus(self, target):
        del target
        return {"focused": True}

    async def press(self, key, target=None, **kwargs):
        del key, target, kwargs
        return {}

    async def scroll(self, query):
        del query
        return {}

    async def upload(self, target, artifact_refs):
        del target, artifact_refs
        return {}

    async def drag(self, source, destination, **kwargs):
        del source, destination, kwargs
        return {}

    async def backfill(self, target, **kwargs):
        del target, kwargs
        return {"paste_accepted": True}

    async def tabs(self, query):
        del query
        return {}

    async def dialog(self, query):
        del query
        return {}

    async def network(self, query):
        del query
        return {}

    async def download(self, query):
        del query
        return {}

    async def wait(self, condition):
        return {"matched": True}

    async def screenshot(self, **kwargs):
        del kwargs
        return b"not-used"

    async def eval(self, query):
        del query
        return {}

    async def analyze(self, query):
        del query
        return {}

    async def aclose(self):
        self.closed = True


class _Factory:
    def __init__(self) -> None:
        self.sessions: list[_Session] = []

    async def create(self, *, run_id: str) -> _Session:
        session = _Session(run_id)
        self.sessions.append(session)
        return session


class _IncompleteSession:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class _IncompleteFactory:
    def __init__(self) -> None:
        self.session = _IncompleteSession()

    async def create(self, *, run_id: str) -> _IncompleteSession:
        del run_id
        return self.session


class _NoopExecutor:
    async def execute(self, arguments, context):
        del arguments, context
        return ToolExecutionResult(status=ToolExecutionStatus.SUCCESS)


def _base_registry() -> ToolRegistry:
    definitions = (
        RegisteredTool(
            ToolDefinition(
                internal_id="test.echo.v1",
                model_alias="echo",
                description="Echo.",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                verification_policy=VerificationPolicy(),
                provenance=ToolProvenance(source="test", reference="echo"),
                version="1.9.0",
            ),
            _NoopExecutor(),
        ),
        RegisteredTool(
            ToolDefinition(
                internal_id="homemaster.observe.v1",
                model_alias="observe",
                description="Observe.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                output_schema={"type": "object"},
                verification_policy=VerificationPolicy(),
                provenance=ToolProvenance(source="test", reference="observe"),
                version="1.9.0",
            ),
            ScreenshotTool(),
        ),
    )
    registry = ToolRegistry()
    registry.register_many([from_registered_tool(tool) for tool in definitions])
    return registry


def _application(
    tmp_path: Path, registry: ToolRegistry, transports: dict[str, _Transport], factory: _Factory
):
    profile = ProviderProfileConfig(
        name="fake",
        protocol="anthropic",
        base_url="https://example.invalid",
        model="fake",
        api_keys=["fake"],
        context_window_tokens=100_000,
    )

    def provider_factory(request, run_id):
        del run_id
        if request.text in {"enabled", "provider-failure", "blocking"}:
            assert factory.sessions, "browser must exist before provider creation"
        if request.text == "provider-failure":
            raise RuntimeError("provider construction failed")
        return transports[request.text]

    def context_factory(request, provider):
        del request
        return ContextAssembler(
            provider=profile,
            policy=ContextPolicyConfig(),
            system_prompt="system",
            summary_client=provider,
        )

    return ApplicationRuntime(
        registry=registry,
        tool_executor=ToolExecutor(registry),
        event_bus=EventBus(),
        session_manager=SessionManager(session_root=tmp_path),
        provider_factory=provider_factory,
        context_assembler_factory=context_factory,
        settings=SimpleNamespace(
            runtime_guards=SimpleNamespace(
                max_consecutive_tool_errors=5,
                max_no_progress_iterations=20,
                reactive_compact_max_retries=2,
            ),
            context=ContextPolicyConfig(),
            provider_name="fake",
        ),
    )


@pytest.mark.asyncio
async def test_enabled_and_disabled_runs_use_isolated_immutable_tool_views(tmp_path: Path) -> None:
    registry = _base_registry()
    original_names = tuple(registry.all_names())
    factory = _Factory()
    transports = {"enabled": _Transport(call_browser=True), "disabled": _Transport()}
    app = _application(tmp_path, registry, transports, factory)

    enabled, disabled = await asyncio.gather(
        app.run(
            RunRequest(
                text="enabled",
                profile="home",
                dependencies={"browser_session_factory": factory},
            )
        ),
        app.run(RunRequest(text="disabled", profile="home")),
    )

    assert enabled.status is RunStatus.REPLIED
    assert disabled.status is RunStatus.REPLIED
    enabled_names = tuple(item["name"] for item in transports["enabled"].tools or [])
    disabled_names = tuple(item["name"] for item in transports["disabled"].tools or [])
    assert set(enabled_names) - set(disabled_names) == {
        "browser_analyze",
        "browser_backfill",
        "browser_check",
        "browser_click",
        "browser_console",
        "browser_dialog",
        "browser_download",
        "browser_drag",
        "browser_extract",
        "browser_fill",
        "browser_find",
        "browser_focus",
        "browser_history",
        "browser_hover",
        "browser_navigate",
        "browser_inspect",
        "browser_network",
        "browser_press",
        "browser_read",
        "browser_screenshot",
        "browser_scroll",
        "browser_select",
        "browser_tabs",
        "browser_type",
        "browser_uncheck",
        "browser_upload",
        "browser_wait",
    }
    assert "browser_eval" not in enabled_names
    assert "observe" not in enabled_names
    assert "observe" in disabled_names
    assert tuple(registry.all_names()) == original_names
    assert len(factory.sessions) == 1
    assert factory.sessions[0].inspect_calls == 1
    assert factory.sessions[0].closed is True
    await app.aclose()


@pytest.mark.asyncio
async def test_provider_failure_closes_created_browser_session(tmp_path: Path) -> None:
    registry = _base_registry()
    factory = _Factory()
    app = _application(tmp_path, registry, {}, factory)

    with pytest.raises(RuntimeError, match="provider construction failed"):
        await app.run(
            RunRequest(
                text="provider-failure",
                dependencies={"browser_session_factory": factory},
            )
        )

    assert len(factory.sessions) == 1
    assert factory.sessions[0].closed is True
    await app.aclose()


@pytest.mark.asyncio
async def test_interface_audit_failure_closes_created_browser_session(tmp_path: Path) -> None:
    registry = _base_registry()
    factory = _IncompleteFactory()
    app = _application(tmp_path, registry, {}, factory)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="missing methods"):
        await app.run(
            RunRequest(
                text="invalid-session",
                dependencies={"browser_session_factory": factory},
            )
        )

    assert factory.session.closed is True
    await app.aclose()


@pytest.mark.asyncio
async def test_application_close_releases_active_browser_session(tmp_path: Path) -> None:
    registry = _base_registry()
    factory = _Factory()
    blocking = _BlockingTransport()
    app = _application(tmp_path, registry, {"blocking": blocking}, factory)
    run = asyncio.create_task(
        app.run(
            RunRequest(
                text="blocking",
                dependencies={"browser_session_factory": factory},
            )
        )
    )
    await asyncio.wait_for(blocking.entered.wait(), timeout=2)

    await app.aclose()

    assert len(factory.sessions) == 1
    assert factory.sessions[0].closed is True
    run.cancel()
    result = await run
    assert result.status is RunStatus.CANCELLED
