from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from homemaster.adapters import alfworld_entry
from homemaster.application import ApplicationSession
from homemaster.config import HomeMasterConfig


def _config(tmp_path: Path, *, memory_enabled: bool = True) -> HomeMasterConfig:
    return HomeMasterConfig.model_validate(
        {
            "memory": {"enabled": memory_enabled},
            "runtime": {"runtime_root": tmp_path / "configured-runs"},
            "observability": {"session_dir": str(tmp_path / "configured-sessions")},
        }
    )


def test_alfworld_entry_uses_full_home_composition_with_isolated_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    application = SimpleNamespace(provider_factory=None)
    bundle = SimpleNamespace(
        application=application,
        mindmemos=object(),
        memory_add_queue=object(),
    )

    def fake_create_home_application(**kwargs: object) -> object:
        calls.update(kwargs)
        return bundle

    monkeypatch.setattr(alfworld_entry, "create_home_application", fake_create_home_application)

    def transport_factory() -> object:
        return object()

    entry = alfworld_entry.AlfworldApplicationEntry(
        config=_config(tmp_path),
        memory_mode="disabled",
        runtime_root=tmp_path / "episode" / "application",
        session_root=tmp_path / "episode" / "sessions",
        transport_factory=transport_factory,
        event_sink=object(),
    )

    assert entry.application is application
    assert calls["tool_environment"] == "alfworld"
    assert calls["runtime_root"] == tmp_path / "episode" / "application"
    assert calls["session_root"] == tmp_path / "episode" / "sessions"
    assert calls["quiet"] is True
    assert application.provider_factory is not None


def test_alfworld_entry_fails_closed_without_mindmemos(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = SimpleNamespace(
        application=SimpleNamespace(provider_factory=None),
        mindmemos=None,
        memory_add_queue=None,
    )
    monkeypatch.setattr(
        alfworld_entry,
        "create_home_application",
        lambda **_kwargs: bundle,
    )

    with pytest.raises(RuntimeError, match="requires embedded MindMemOS"):
        alfworld_entry.AlfworldApplicationEntry(
            config=_config(tmp_path, memory_enabled=False),
            memory_mode="disabled",
            runtime_root=tmp_path / "application",
            session_root=tmp_path / "sessions",
            transport_factory=None,
            event_sink=object(),
        )


def test_alfworld_entry_rejects_legacy_memory_mode(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="legacy ALFWorld memory_mode"):
        alfworld_entry.AlfworldApplicationEntry(
            config=_config(tmp_path),
            memory_mode="full",
            runtime_root=tmp_path / "application",
            session_root=tmp_path / "sessions",
            transport_factory=None,
            event_sink=object(),
        )


def test_alfworld_entry_closes_open_session_before_application_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    class Application:
        provider_factory = None

        def __init__(self) -> None:
            self.session_end_handler = lambda session_id, reason: calls.append(
                ("session_end", session_id, reason)
            )

        def session(self, session_id, *, exit_reason="session_end"):
            return ApplicationSession(self, session_id, exit_reason)

        async def aclose(self):
            calls.append(("application_close",))

    application = Application()
    monkeypatch.setattr(
        alfworld_entry,
        "create_home_application",
        lambda **_kwargs: SimpleNamespace(
            application=application,
            mindmemos=object(),
            memory_add_queue=object(),
        ),
    )
    entry = alfworld_entry.AlfworldApplicationEntry(
        config=_config(tmp_path),
        memory_mode="disabled",
        runtime_root=tmp_path / "application",
        session_root=tmp_path / "sessions",
        transport_factory=None,
        event_sink=object(),
    )

    entry.begin_session("episode-one")
    entry.close()

    assert calls == [
        ("session_end", "episode-one", "alfworld_episode_end"),
        ("application_close",),
    ]
