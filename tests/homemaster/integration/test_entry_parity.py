"""V1.9 entry composition and session parity regressions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from homemaster.agent.messages import Message
from homemaster.application import RunRequest, RunStatus
from homemaster.cli.composition import HomeCliBackend, create_home_application
from homemaster.config import load_config
from homemaster.providers.transports import TransportDelta

EXAMPLE_CONFIG = Path(__file__).parents[3] / "config/homemaster.example.yaml"


class FakeTransport:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.closed = 0

    async def stream(
        self,
        messages: list[Message],
        tools=None,
        *,
        attempt_sink=None,
        **kwargs,
    ) -> AsyncIterator[TransportDelta]:
        del messages, tools, attempt_sink, kwargs
        reply = self.replies.pop(0)
        yield TransportDelta(type="text", text_delta=reply, finish_reason="stop")

    async def complete(self, messages, **kwargs):
        del messages, kwargs
        from homemaster.agent.messages import AssistantMessage, ContentBlock

        return AssistantMessage(content=[ContentBlock(text="compact summary")])

    def close(self) -> None:
        self.closed += 1


def _config(tmp_path: Path):
    config = load_config(EXAMPLE_CONFIG)
    observability = config.observability.model_copy(
        update={"session_dir": str(tmp_path / "sessions")}
    )
    runtime = config.runtime.model_copy(update={"runtime_root": tmp_path / "runs"})
    return config.model_copy(update={"observability": observability, "runtime": runtime})


def test_home_outer_composition_runs_one_typed_request_and_closes_owned_provider(
    tmp_path,
) -> None:
    bundle = create_home_application(config=_config(tmp_path), run_label="one-shot")
    provider = FakeTransport(["hello"])
    bundle.application.provider_factory = lambda request, run_id: provider

    async def execute():
        result = await bundle.application.run(
            RunRequest(
                text="hello",
                session_id="entry-one",
                profile="home",
                environment=HomeCliBackend(world_path=None, memory_path=None),
            )
        )
        await bundle.application.aclose()
        return result

    result = asyncio.run(execute())

    assert result.status is RunStatus.REPLIED
    assert result.final_reply == "hello"
    assert provider.closed == 1
    assert bundle.trace_path.is_file()


def test_compact_persists_revision_then_process_rebuild_resumes_same_session(
    tmp_path,
) -> None:
    config = _config(tmp_path)
    first = create_home_application(config=config, run_label="first-process")
    first.application.provider_factory = lambda request, run_id: FakeTransport(["first"])

    async def first_process():
        initial = await first.application.run(
            RunRequest(
                text="first",
                session_id="resumable",
                profile="home",
                environment=HomeCliBackend(world_path=None, memory_path=None),
            )
        )
        compact = await first.application.compact("resumable")
        await first.application.aclose()
        return initial, compact

    initial, compact = asyncio.run(first_process())
    second = create_home_application(config=config, run_label="second-process")
    second.application.provider_factory = lambda request, run_id: FakeTransport(["second"])

    async def second_process():
        resumed = await second.application.run(
            RunRequest(
                text="second",
                session_id="resumable",
                profile="home",
                resume=True,
                environment=HomeCliBackend(world_path=None, memory_path=None),
            )
        )
        revision = second.application.status("resumable").revision
        messages = second.application.session_manager.get("resumable").session.messages
        await second.application.aclose()
        return resumed, revision, messages

    resumed, revision, messages = asyncio.run(second_process())

    assert initial.status is RunStatus.REPLIED
    assert compact.revision == 2
    assert resumed.status is RunStatus.REPLIED
    assert revision == 3
    assert [message.role for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
