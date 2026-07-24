"""Child-session orchestration for one full coworker ticket turn."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from homemaster.adapters.coworker_entry import (
    CoworkerApplicationEntry,
    build_coworker_transport_factory,
)
from homemaster.adapters.profiles import CoworkerScreenshotBackend
from homemaster.adapters.thread_owned_sync import (
    ThreadOwnedScreenshotBackend,
    ThreadOwnedSyncBackendAdapter,
)
from homemaster.application import (
    ResourceCleanupError,
    RunPolicy,
    RunRequest,
    RuntimeStopDecision,
)
from homemaster.benchmarking.coworker_demo.browser_driver import PlaywrightBrowserDriver
from homemaster.benchmarking.coworker_demo.budget import CoworkerBudget
from homemaster.benchmarking.coworker_demo.config import load_coworker_config
from homemaster.benchmarking.coworker_demo.environment_client import (
    EnvironmentClient,
    EnvironmentProcess,
)
from homemaster.benchmarking.coworker_demo.prompt import SYSTEM_PROMPT, build_task_prompt
from homemaster.benchmarking.coworker_demo.skills import load_coworker_skills
from homemaster.benchmarking.coworker_demo.ticket_bundle import CaseRepository
from homemaster.benchmarking.coworker_demo.tracing import CoworkerTraceSink
from homemaster.benchmarking.coworker_demo.types import (
    CoworkerAttemptError,
    CoworkerOutcome,
    CoworkerTurnResult,
    ValidTicketRoute,
)
from homemaster.config import HOMEMASTER_CONFIG_PATH, load_config
from homemaster.events.sinks import ConsoleEventSink, FanoutEventSink
from homemaster.providers.attempts import JsonlProviderAttemptSink


def new_coworker_run_id() -> str:
    return f"coworker-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _update_attempt_manifest(artifact_root: Path, **updates: Any) -> dict[str, Any]:
    path = artifact_root / "attempt_manifest.json"
    current: dict[str, Any] = {}
    if path.is_file():
        current = json.loads(path.read_text(encoding="utf-8"))
    allowed_fields = {
        "schema_version",
        "run_id",
        "run_root",
        "scenario_id",
        "status",
        "started_at_utc",
        "error_type",
        "formal_success",
        "video_path",
        "provider_identity_path",
    }
    current.update({key: value for key, value in updates.items() if key in allowed_fields})
    current["updated_at_utc"] = datetime.now(UTC).isoformat()
    _atomic_json(path, current)
    return current


def _safe_provider_identity(
    provider: Any,
    *,
    provider_config_override: bool,
) -> dict[str, Any]:
    endpoint = urlsplit(str(provider.base_url))
    nonsecret = {
        "provider": str(provider.name),
        "model": str(provider.model),
        "api_format": str(provider.api_format),
        "transport": str(provider.transport),
        "scheme": endpoint.scheme.lower(),
        "host": (endpoint.hostname or "").lower(),
        "api_key_count": len(provider.api_keys),
        "provider_config_override": provider_config_override,
    }
    fingerprint_source = json.dumps(
        nonsecret, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "schema_version": 1,
        "created_at_utc": datetime.now(UTC).isoformat(),
        **nonsecret,
        "config_fingerprint_sha256": hashlib.sha256(fingerprint_source).hexdigest(),
    }


def _resolve_configured_bundle(route: ValidTicketRoute, data_root: Path):
    configured_root = data_root.resolve()
    if route.case_root.resolve() != configured_root:
        raise ValueError("ticket bundle root does not match configured coworker data_root")
    bundle = CaseRepository(configured_root).resolve(route.ticket_path, route.scenario_id)
    if dict(bundle.locked_hashes) != route.locked_hashes:
        raise ValueError("ticket bundle hashes changed after routing")
    return bundle


def run_coworker_turn(
    route: ValidTicketRoute,
    *,
    coworker_config_path: Path = Path("config/coworker_demo.yaml"),
    provider_config_path: Path | None = None,
) -> CoworkerTurnResult:
    configured_coworker = os.environ.get("HOMEMASTER_COWORKER_CONFIG")
    if configured_coworker:
        coworker_config_path = Path(configured_coworker)
    configured_provider = os.environ.get("HOMEMASTER_COWORKER_PROVIDER_CONFIG")
    if provider_config_path is None and configured_provider:
        provider_config_path = Path(configured_provider)
    config = load_coworker_config(coworker_config_path)
    bundle = _resolve_configured_bundle(route, config.paths.data_root)
    run_id = new_coworker_run_id()
    run_root = config.paths.artifact_root / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    _update_attempt_manifest(
        run_root,
        schema_version=1,
        run_id=run_id,
        run_root=str(run_root),
        scenario_id=route.scenario_id,
        status="allocated",
        started_at_utc=datetime.now(UTC).isoformat(),
    )
    transcript_path = run_root / "agent/cli_transcript.log"
    trace_path = run_root / "agent/runtime_events.jsonl"
    outcome = CoworkerOutcome()
    budget = CoworkerBudget(
        max_wall_time_s=config.runtime.max_wall_time_s,
        max_browser_actions=config.runtime.max_browser_actions,
        max_terminal_actions=config.runtime.max_terminal_actions,
    )
    client = EnvironmentClient(
        config.service.public_base_url,
        timeout_s=config.service.request_timeout_s,
        budget=budget,
        outcome=outcome,
    )
    service = EnvironmentProcess(config, log_dir=run_root / "environment/process")
    driver: PlaywrightBrowserDriver | None = None
    sync_backend: ThreadOwnedSyncBackendAdapter | None = None
    recording_started = False
    recording_stopped = False
    process_returns: dict[str, int | None] = {}
    runtime_result: Any = None
    summary: dict[str, Any] = {
        "trajectory_score": 0.0,
        "result_score": 0.0,
        "overall_score": 0.0,
        "formal_success": False,
    }
    try:
        service.start(client)
        _update_attempt_manifest(run_root, status="service_started")
        client.create_run(run_id, route.scenario_id, dict(bundle.locked_hashes))
        _update_attempt_manifest(run_root, status="environment_created")
        recording = client.start_recording(run_id)
        recording_started = True
        _update_attempt_manifest(run_root, status="recording_started")
        display = str(recording["display"])
        sync_backend = ThreadOwnedSyncBackendAdapter(name=run_id)
        driver = sync_backend.call(
            PlaywrightBrowserDriver,
            run_id=run_id,
            base_url=config.service.public_base_url,
            display=display,
            chrome_executable=config.browser.chrome_executable,
            profile_dir=run_root / "browser/agent-profile",
            trace_path=run_root / "browser/playwright_trace.zip",
            client=client,
            timeout_s=config.browser.action_timeout_s,
            window_x=config.browser.window_x,
            window_y=config.browser.window_y,
            width=config.browser.viewport_width,
            height=config.browser.viewport_height,
        )
        runtime_result = _run_runtime(
            run_id=run_id,
            run_root=run_root,
            client=client,
            driver=driver,
            sync_backend=sync_backend,
            budget=budget,
            outcome=outcome,
            provider_config_path=provider_config_path,
            max_tool_iterations=config.runtime.max_tool_iterations,
            transcript_path=transcript_path,
            trace_path=trace_path,
            ticket_url=f"{config.service.public_base_url}/ticket/{run_id}",
        )
        _update_attempt_manifest(run_root, status="runtime_completed")
        if not outcome.terminal:
            outcome.classification = "premature_reply"
        frozen = client.finalize(run_id)["summary"]
        _update_attempt_manifest(run_root, status="presentation_finalized")
        _append_transcript(
            transcript_path,
            "FROZEN SCORES "
            f"trajectory={frozen['trajectory_score']:.1f} result={frozen['result_score']:.1f} "
            "video_verification=pending formal_success=pending",
        )
        time.sleep(config.recording.final_score_hold_s)
        sync_backend.call(driver.close)
        driver = None
        stopped = client.stop_recording(run_id)
        recording_stopped = True
        _update_attempt_manifest(run_root, status="recording_verified")
        summary = stopped["summary"]
        process_returns.update(stopped.get("display_return_codes", {}))
    except Exception as exc:
        try:
            _update_attempt_manifest(
                run_root,
                status="failed",
                error_type=type(exc).__name__,
            )
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        raise CoworkerAttemptError(
            run_id=run_id,
            run_root=run_root,
            error_type=type(exc).__name__,
        ) from exc
    finally:
        cleanup_errors: list[BaseException] = []
        active_error = sys.exception()
        if driver is not None:
            try:
                assert sync_backend is not None
                sync_backend.call(driver.close)
            except BaseException as exc:
                cleanup_errors.append(exc)
        worker_stopped = sync_backend is None
        if sync_backend is not None:
            try:
                sync_backend.close()
                worker_stopped = not sync_backend.alive
            except BaseException as exc:
                cleanup_errors.append(exc)
        if worker_stopped:
            if recording_started and not recording_stopped:
                try:
                    client.finalize(run_id)
                    client.stop_recording(run_id)
                except BaseException as exc:
                    cleanup_errors.append(exc)
            try:
                process_returns["service"] = service.stop()
            except BaseException as exc:
                cleanup_errors.append(exc)
            try:
                client.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
            _write_process_returns(run_root, process_returns)
        if cleanup_errors:
            cleanup = ResourceCleanupError(tuple(cleanup_errors))
            if active_error is not None:
                active_error.add_note(str(cleanup))
                active_error.cleanup_error = cleanup  # type: ignore[attr-defined]
            else:
                raise cleanup
    status = "completed" if summary.get("formal_success") else "failed"
    _update_attempt_manifest(
        run_root,
        status=status,
        formal_success=bool(summary.get("formal_success")),
        video_path=str(run_root / "video/demo.mp4"),
    )
    final_reply = (
        runtime_result.final_reply
        if runtime_result is not None and runtime_result.final_reply
        else f"Coworker run ended with {outcome.classification or status}."
    )
    return CoworkerTurnResult(
        run_id=run_id,
        status=status,
        final_reply=final_reply,
        trajectory_score=float(summary["trajectory_score"]),
        result_score=float(summary["result_score"]),
        overall_score=float(summary["overall_score"]),
        formal_success=bool(summary.get("formal_success")),
        artifact_path=run_root,
        video_path=run_root / "video/demo.mp4",
        trace_path=trace_path,
        classification=outcome.classification,
        process_returns=process_returns,
    )


def _run_runtime(
    *,
    run_id: str,
    run_root: Path,
    client: EnvironmentClient,
    driver: PlaywrightBrowserDriver,
    sync_backend: ThreadOwnedSyncBackendAdapter,
    budget: CoworkerBudget,
    outcome: CoworkerOutcome,
    provider_config_path: Path | None,
    max_tool_iterations: int,
    transcript_path: Path,
    trace_path: Path,
    ticket_url: str,
):
    home_config = load_config(provider_config_path or HOMEMASTER_CONFIG_PATH)
    provider = home_config.get_provider(
        home_config.runtime_defaults.default_provider_name, kind="chat"
    )
    provider_identity = _safe_provider_identity(
        provider,
        provider_config_override=provider_config_path is not None,
    )
    _atomic_json(run_root / "agent/provider_identity.json", provider_identity)
    _update_attempt_manifest(
        run_root,
        status="provider_identity_recorded",
        provider_identity_path="agent/provider_identity.json",
    )
    observability = home_config.observability.model_copy(
        update={
            "trace_dir": str(run_root / "agent/trace"),
            "session_dir": str(run_root / "agent/session"),
        }
    )
    home_config = home_config.model_copy(update={"observability": observability})
    trace_sink = CoworkerTraceSink(
        trace_path,
        client,
        run_id,
        transcript_path=transcript_path,
        sensitive_values=tuple(provider.api_keys),
    )
    event_sink = FanoutEventSink([trace_sink, ConsoleEventSink(show_replies=False)])
    skill_registry = load_coworker_skills()
    backend = ThreadOwnedScreenshotBackend(
        CoworkerScreenshotBackend(driver=driver, domain_run_id=run_id),
        sync_backend,
    )

    def stop_condition(_context: Any):
        if not outcome.terminal:
            return None
        return RuntimeStopDecision(
            status="replied",
            final_reply=f"Change run reached terminal decision: {outcome.decision}.",
            payload={"classification": outcome.classification},
        )

    entry = CoworkerApplicationEntry(
        config=home_config,
        provider_profile=provider,
        system_prompt=SYSTEM_PROMPT,
        run_root=run_root,
        transport_factory=build_coworker_transport_factory(
            provider_profile=provider,
            budget=budget,
            outcome=outcome,
            timeout_s=budget.timeout(home_config.provider_client.timeout_s),
        ),
        event_sink=event_sink,
        sync_backend_adapter=sync_backend,
    )
    try:
        return entry.run(
            RunRequest(
                text=build_task_prompt(run_id, ticket_url),
                session_id=run_id,
                profile="coworker",
                provider_name=provider.name,
                environment=backend,
                run_policy=RunPolicy(
                    max_tool_iterations=max_tool_iterations,
                    deadline_s=max(budget.remaining_s, 0.001),
                    stop_condition=stop_condition,
                ),
                dependencies={
                    "skill_registry": skill_registry,
                    "coworker_environment": client,
                    "coworker_browser": driver,
                    "coworker_budget": budget,
                    "coworker_outcome": outcome,
                    "sync_backend_adapter": sync_backend,
                    "external_terminal_owner": _CoworkerTerminalOwner(outcome),
                    "provider_attempt_sink_factory": lambda: JsonlProviderAttemptSink(
                        run_root / "agent/provider_attempts.jsonl"
                    ),
                },
            )
        )
    finally:
        entry.close()


class _CoworkerTerminalOwner:
    def __init__(self, outcome: CoworkerOutcome) -> None:
        self._outcome = outcome

    @property
    def succeeded(self) -> bool:
        return self._outcome.terminal


def _append_transcript(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()


def _write_process_returns(run_root: Path, returns: dict[str, int | None]) -> None:
    path = run_root / "environment/process_returns.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(returns, sort_keys=True) + "\n", encoding="utf-8")
