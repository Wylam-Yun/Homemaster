"""Replay LoCoMo conversations through the production HomeMaster application."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from homemaster.application import RunPolicy, RunRequest, RunStatus
from homemaster.cli.composition import create_home_application
from homemaster.events.event_payloads import trace_event_payload
from homemaster.experience import FinalizeResult, SessionFinalizer
from homemaster.tools.contracts import PermissionSubject

_SESSION_KEY = re.compile(r"session_(\d+)$")
_SOURCE_DATE_FORMAT = "%I:%M %p on %d %B, %Y"
_SUCCESS_STATUSES = {RunStatus.REPLIED, RunStatus.COMPLETED}


@dataclass(frozen=True)
class LocomoTurn:
    dia_id: str
    speaker: str
    text: str
    image_caption: str | None = None


@dataclass(frozen=True)
class LocomoSession:
    source_index: int
    source_date: str
    source_timestamp: str
    turns: tuple[LocomoTurn, ...]
    partial: bool = False


@dataclass(frozen=True)
class LocomoQuestion:
    question: str
    evidence: tuple[str, ...]
    category: int | str | None


@dataclass(frozen=True)
class LocomoSelection:
    sample_id: str
    focal_speaker: str
    other_speaker: str
    sessions: tuple[LocomoSession, ...]
    source_turn_count: int
    questions: tuple[LocomoQuestion, ...]


@dataclass(frozen=True)
class LocomoBenchmarkConfig:
    data_file: Path
    sample_id: str
    focal_speaker: str
    trace_root: Path
    home_config: Any
    max_source_turns: int = 100
    qa_probes: int = 10
    run_deadline_seconds: float = 300.0
    run_id: str | None = None
    provider_name: str | None = None
    model_override: str | None = None

    def __post_init__(self) -> None:
        if self.max_source_turns <= 0:
            raise ValueError("max_source_turns must be positive")
        if self.qa_probes < 0:
            raise ValueError("qa_probes must not be negative")
        if self.run_deadline_seconds <= 0:
            raise ValueError("run_deadline_seconds must be positive")


class HistoricalTraceSink:
    """Persist runtime events using their LoCoMo source-session timestamps."""

    def __init__(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        self.path = output_dir / "runtime_events.jsonl"
        self.finalizer_path = output_dir / "finalizer_events.jsonl"
        self._handle = self.path.open("x", encoding="utf-8")
        self._finalizer_handle = self.finalizer_path.open("x", encoding="utf-8")
        self._timestamps: dict[str, str] = {}
        self._finalizer_texts: dict[str, str] = {}
        self._events: list[dict[str, Any]] = []

    def bind(
        self,
        session_id: str,
        source_timestamp: str,
        *,
        finalizer_user_text: str | None = None,
    ) -> None:
        self._timestamps[session_id] = source_timestamp
        if finalizer_user_text is not None:
            self._finalizer_texts[session_id] = finalizer_user_text

    def emit(self, event: Any) -> None:
        timestamp = self._timestamps.get(event.session_id, event.timestamp)
        copied = replace(event, timestamp=timestamp)
        entry = asdict(copied)
        entry["payload"] = trace_event_payload(copied.payload)
        self._events.append(entry)
        self._handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._handle.flush()
        finalizer_entry = entry
        finalizer_text = self._finalizer_texts.get(event.session_id)
        if event.type == "runtime.turn_started" and finalizer_text is not None:
            finalizer_entry = dict(entry)
            finalizer_payload = dict(entry["payload"])
            finalizer_payload["user_text"] = finalizer_text
            finalizer_entry["payload"] = finalizer_payload
        self._finalizer_handle.write(json.dumps(finalizer_entry, ensure_ascii=False) + "\n")
        self._finalizer_handle.flush()

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._events)

    def close(self) -> None:
        self._handle.close()
        self._finalizer_handle.close()


def load_locomo_selection(
    data_file: Path,
    *,
    sample_id: str,
    focal_speaker: str,
    max_source_turns: int,
    qa_probes: int = 10,
) -> LocomoSelection:
    if max_source_turns <= 0:
        raise ValueError("max_source_turns must be positive")
    payload = json.loads(data_file.read_text(encoding="utf-8"))
    sample = next((item for item in payload if item.get("sample_id") == sample_id), None)
    if sample is None:
        raise ValueError(f"LoCoMo sample not found: {sample_id}")
    conversation = sample.get("conversation")
    if not isinstance(conversation, dict):
        raise ValueError(f"LoCoMo sample has no conversation: {sample_id}")
    speakers = (conversation.get("speaker_a"), conversation.get("speaker_b"))
    if focal_speaker not in speakers:
        raise ValueError(
            f"focal speaker {focal_speaker!r} is not one of {speakers!r} in {sample_id}"
        )
    other_speaker = str(speakers[1] if speakers[0] == focal_speaker else speakers[0])

    candidates: list[tuple[datetime, int, str, list[dict[str, Any]]]] = []
    for key, value in conversation.items():
        matched = _SESSION_KEY.fullmatch(key)
        if matched is None or not isinstance(value, list):
            continue
        index = int(matched.group(1))
        source_date = conversation.get(f"session_{index}_date_time")
        if not isinstance(source_date, str):
            raise ValueError(f"missing date for {sample_id} session_{index}")
        parsed = datetime.strptime(source_date, _SOURCE_DATE_FORMAT).replace(tzinfo=UTC)
        candidates.append((parsed, index, source_date, value))
    candidates.sort(key=lambda item: (item[0], item[1]))

    sessions: list[LocomoSession] = []
    included_ids: set[str] = set()
    remaining = max_source_turns
    for source_time, index, source_date, raw_turns in candidates:
        if remaining == 0:
            break
        selected_turns = raw_turns[:remaining]
        turns: list[LocomoTurn] = []
        for raw in selected_turns:
            dia_id = str(raw.get("dia_id") or "").strip()
            speaker = str(raw.get("speaker") or "").strip()
            text = str(raw.get("text") or "").strip()
            if not dia_id or not speaker or not text:
                raise ValueError(f"invalid turn in {sample_id} session_{index}")
            caption = raw.get("blip_caption")
            turn = LocomoTurn(
                dia_id=dia_id,
                speaker=speaker,
                text=text,
                image_caption=str(caption).strip() if caption else None,
            )
            turns.append(turn)
            included_ids.add(dia_id)
        sessions.append(
            LocomoSession(
                source_index=index,
                source_date=source_date,
                source_timestamp=source_time.isoformat(),
                turns=tuple(turns),
                partial=len(selected_turns) < len(raw_turns),
            )
        )
        remaining -= len(selected_turns)

    questions: list[LocomoQuestion] = []
    for raw in sample.get("qa", []) if qa_probes else ():
        evidence = tuple(str(value) for value in raw.get("evidence", ()))
        question = raw.get("question")
        if not isinstance(question, str) or not question.strip() or not evidence:
            continue
        if not set(evidence).issubset(included_ids):
            continue
        questions.append(
            LocomoQuestion(
                question=question.strip(),
                evidence=evidence,
                category=raw.get("category"),
            )
        )
        if len(questions) == qa_probes:
            break
    return LocomoSelection(
        sample_id=sample_id,
        focal_speaker=focal_speaker,
        other_speaker=other_speaker,
        sessions=tuple(sessions),
        source_turn_count=sum(len(session.turns) for session in sessions),
        questions=tuple(questions),
    )


def render_session_transcript(selection: LocomoSelection, session: LocomoSession) -> str:
    transcript: list[str] = []
    for turn in session.turns:
        line = f"{turn.speaker}: {turn.text}"
        if turn.image_caption:
            line += f"\n[Image shared by {turn.speaker}: {turn.image_caption}]"
        transcript.append(line)
    return "\n".join(transcript)


def render_session_prompt(selection: LocomoSelection, session: LocomoSession) -> str:
    return (
        f"Historical conversation between {selection.focal_speaker} and "
        f"{selection.other_speaker}; recorded at {session.source_date}.\n"
        "Please process this historical conversation and summarize its durable facts.\n\n"
        + render_session_transcript(selection, session)
    )


class LocomoBenchmarkRunner:
    def __init__(self, config: LocomoBenchmarkConfig) -> None:
        self.config = config

    def run(self) -> dict[str, Any]:
        return asyncio.run(self._run())

    async def _run(self) -> dict[str, Any]:
        selection = load_locomo_selection(
            self.config.data_file,
            sample_id=self.config.sample_id,
            focal_speaker=self.config.focal_speaker,
            max_source_turns=self.config.max_source_turns,
            qa_probes=self.config.qa_probes,
        )
        run_id = self.config.run_id or f"locomo-{uuid.uuid4().hex[:12]}"
        run_dir = self.config.trace_root.expanduser().resolve() / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        trace = HistoricalTraceSink(run_dir / "runtime")
        sessions_path = run_dir / "sessions.jsonl"
        memory_events_path = run_dir / "memory_events.jsonl"
        summary_path = run_dir / "summary.json"
        session_records: list[dict[str, Any]] = []
        finalization_records: list[dict[str, Any]] = []
        qa_records: list[dict[str, Any]] = []
        failure: str | None = None
        bundle = create_home_application(
            config=self.config.home_config,
            run_label=run_id,
            quiet=True,
            event_sink=trace,
            tool_environment=None,
        )
        if bundle.mindmemos is None or bundle.memory_add_queue is None:
            trace.close()
            raise RuntimeError("LoCoMo benchmark requires HomeMaster memory to be enabled")
        application = bundle.application
        memory_tenant_id = _memory_scope_key(selection.focal_speaker)
        finalizer = SessionFinalizer(
            trace_path=trace.finalizer_path,
            data_root=bundle.config.memory.data_root,
            mindmemos=bundle.mindmemos,
            memory_tenant_id=memory_tenant_id,
            dreaming_coordinator=bundle.dreaming_coordinator,
            event_sink=application.event_bus,
        )
        subject = _benchmark_subject(memory_tenant_id)
        try:
            for session in selection.sessions:
                session_id = f"{run_id}-{selection.sample_id}-source-{session.source_index}"
                trace.bind(
                    session_id,
                    session.source_timestamp,
                    finalizer_user_text=render_session_transcript(selection, session),
                )
                result = await application.run(
                    RunRequest(
                        text=render_session_prompt(selection, session),
                        session_id=session_id,
                        profile="home",
                        provider_name=self.config.provider_name,
                        model_override=self.config.model_override,
                        run_policy=RunPolicy(
                            max_tool_iterations=bundle.config.runtime.max_tool_iterations,
                            deadline_s=self.config.run_deadline_seconds,
                        ),
                        permission_subject=subject,
                        metadata={
                            "benchmark": "locomo",
                            "sample_id": selection.sample_id,
                            "source_session": session.source_index,
                        },
                    )
                )
                record = {
                    "record_type": "source_session",
                    "sample_id": selection.sample_id,
                    "focal_speaker": selection.focal_speaker,
                    "source_session": session.source_index,
                    "source_date": session.source_date,
                    "source_timestamp": session.source_timestamp,
                    "source_turns": len(session.turns),
                    "partial": session.partial,
                    "session_id": result.session_id,
                    "run_id": result.run_id,
                    "runtime_status": str(result.status),
                    "runtime_error_code": result.error_code,
                    "reply": result.final_reply,
                }
                if result.status not in _SUCCESS_STATUSES:
                    session_records.append(record)
                    _append_jsonl(sessions_path, record)
                    raise RuntimeError(
                        f"HomeMaster run failed for source session {session.source_index}: "
                        f"status={result.status}, error={result.error_code}"
                    )
                finalized = await _enqueue_finalization(
                    bundle.memory_add_queue,
                    finalizer,
                    session_id,
                )
                record["finalization"] = _finalize_result_dict(finalized)
                record["terminal_memory_readback"] = await _readback_operations(
                    bundle.mindmemos,
                    memory_tenant_id,
                    session_id,
                    finalized,
                )
                session_records.append(record)
                finalization_records.append(
                    {
                        "record_type": "session_finalization",
                        "source_session": session.source_index,
                        "source_timestamp": session.source_timestamp,
                        **_finalize_result_dict(finalized),
                    }
                )
                _append_jsonl(sessions_path, record)
                if finalized.status not in {"completed", "already_completed"}:
                    raise RuntimeError(
                        f"Session finalization failed for source session {session.source_index}: "
                        f"{finalized.error or finalized.status}"
                    )

            for index, question in enumerate(selection.questions, start=1):
                session_id = f"{run_id}-{selection.sample_id}-qa-{index}"
                result = await application.run(
                    RunRequest(
                        text=(
                            f"Answer this question about {selection.focal_speaker}'s prior "
                            "conversation. Use recalled long-term memory or a memory search tool "
                            f"when needed.\n\nQuestion: {question.question}"
                        ),
                        session_id=session_id,
                        profile="home",
                        provider_name=self.config.provider_name,
                        model_override=self.config.model_override,
                        run_policy=RunPolicy(
                            max_tool_iterations=bundle.config.runtime.max_tool_iterations,
                            deadline_s=self.config.run_deadline_seconds,
                        ),
                        permission_subject=subject,
                        metadata={
                            "benchmark": "locomo",
                            "sample_id": selection.sample_id,
                            "qa_probe": index,
                        },
                    )
                )
                qa_record = {
                    "record_type": "qa_probe",
                    "probe_index": index,
                    "question": question.question,
                    "evidence": list(question.evidence),
                    "category": question.category,
                    "session_id": result.session_id,
                    "run_id": result.run_id,
                    "runtime_status": str(result.status),
                    "runtime_error_code": result.error_code,
                    "reply": result.final_reply,
                }
                qa_records.append(qa_record)
                _append_jsonl(sessions_path, qa_record)
                if result.status not in _SUCCESS_STATUSES:
                    raise RuntimeError(
                        f"HomeMaster QA probe {index} failed: "
                        f"status={result.status}, error={result.error_code}"
                    )
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                await application.aclose()
            except Exception as exc:
                close_failure = f"{type(exc).__name__}: {exc}"
                failure = (
                    f"{failure}; application close: {close_failure}"
                    if failure
                    else close_failure
                )
            trace.close()

        relevant = [
            _memory_event_record(event) for event in trace.events if _is_memory_event(event)
        ]
        for record in [*relevant, *finalization_records]:
            _append_jsonl(memory_events_path, record)
        completed_source_records = [
            record
            for record in session_records
            if record.get("finalization", {}).get("finalization_status")
            in {"completed", "already_completed"}
        ]
        summary = {
            "run_id": run_id,
            "status": "failed" if failure else "completed",
            "failure": failure,
            "sample_id": selection.sample_id,
            "focal_speaker": selection.focal_speaker,
            "memory_tenant_id": memory_tenant_id,
            "source_turn_count": selection.source_turn_count,
            "source_session_count": len(selection.sessions),
            "completed_source_turn_count": sum(
                int(record["source_turns"]) for record in completed_source_records
            ),
            "completed_source_session_count": len(completed_source_records),
            "attempted_source_session_count": len(session_records),
            "qa_probe_count": len(qa_records),
            "feature_counts": _feature_counts(trace.events, finalization_records),
            "artifacts": {
                "sessions": str(sessions_path),
                "memory_events": str(memory_events_path),
                "runtime_events": str(trace.path),
                "finalizer_events": str(trace.finalizer_path),
            },
        }
        _write_json(summary_path, summary)
        if failure:
            raise RuntimeError(f"LoCoMo benchmark failed; see {summary_path}: {failure}")
        return summary


async def _enqueue_finalization(
    queue: Any,
    finalizer: SessionFinalizer,
    session_id: str,
) -> FinalizeResult:
    completed: dict[str, FinalizeResult] = {}

    async def work() -> None:
        completed["result"] = await finalizer.finalize(session_id, "locomo_source_session_end")

    queue.enqueue_work(
        job_type="session_finalization",
        session_id=session_id,
        work=work,
    )
    await queue.wait_idle()
    result = completed.get("result")
    if result is None:
        raise RuntimeError(f"memory queue did not return finalization result for {session_id}")
    return result


async def _readback_operations(
    mindmemos: Any,
    tenant_id: str,
    session_id: str,
    result: FinalizeResult,
) -> list[dict[str, Any]]:
    from mindmemos.typing import MemoryRequestContext

    context = MemoryRequestContext(
        request_id=f"locomo-readback:{session_id}",
        account_id=tenant_id,
        project_id=tenant_id,
        api_key_uuid="embedded-local",
        user_id=tenant_id,
        app_id="homemaster",
        session_id=session_id,
        agent_id="homemaster",
    )
    readback: list[dict[str, Any]] = []
    for operation in result.operations:
        if not operation.memory_id:
            continue
        raw = await mindmemos.get_raw(operation.memory_id, context)
        status = getattr(raw, "status", None) if raw is not None else None
        if status not in {"active", "archived"}:
            raise RuntimeError(
                f"terminal memory readback failed for {operation.memory_id}: status={status!r}"
            )
        readback.append(
            {
                "memory_id": operation.memory_id,
                "operation": operation.operation,
                "status": status,
            }
        )
    return readback


def _memory_scope_key(focal_speaker: str) -> str:
    """Map a display name to HomeMaster's normalized tenant token."""
    token = re.sub(r"[^a-z0-9_.:/@+-]+", "-", focal_speaker.strip().casefold()).strip("-")
    if not token or not token[0].isalpha():
        raise ValueError(
            f"focal speaker cannot produce a valid memory tenant id: {focal_speaker!r}"
        )
    return token


def _benchmark_subject(memory_tenant_id: str) -> PermissionSubject:
    default = RunRequest(text="permission template").permission_subject
    return PermissionSubject(
        subject_id=f"locomo-{memory_tenant_id}",
        channel="benchmark",
        roles=default.roles,
        tenant_id=memory_tenant_id,
        capabilities=default.capabilities,
    )


def _finalize_result_dict(result: FinalizeResult) -> dict[str, Any]:
    return {
        "finalization_status": result.status,
        "collected_events": result.collected_events,
        "excluded_transport_deltas": result.excluded_transport_deltas,
        "rendered_messages": result.rendered_messages,
        "duration_ms": result.duration_ms,
        "operations": [asdict(operation) for operation in result.operations],
        "error": result.error,
    }


def _is_memory_event(event: dict[str, Any]) -> bool:
    return str(event.get("type", "")).startswith("memory.") or (
        str(event.get("type", "")).startswith("tool.call_")
        and str(event.get("name", "")).startswith("mindmemos_")
    )


def _memory_event_record(event: dict[str, Any]) -> dict[str, Any]:
    return {"record_type": "runtime_memory_event", **event}


def _feature_counts(
    events: tuple[dict[str, Any], ...],
    finalizations: list[dict[str, Any]],
) -> dict[str, int]:
    types = [str(event.get("type")) for event in events]
    diagnostics = [
        diagnostic
        for event in events
        for diagnostic in (event.get("payload", {}).get("data", {}).get("diagnostics", ()))
        if isinstance(diagnostic, dict)
    ]
    return {
        "vanilla_add_operations": sum(
            len(record.get("operations", ())) for record in finalizations
        ),
        "automatic_recall": types.count("memory.automatic_recall"),
        "memory_tool_calls": sum(
            event.get("type") == "tool.call_started"
            and str(event.get("name", "")).startswith("mindmemos_")
            for event in events
        ),
        "explicit_feedback_completed": types.count("memory.feedback.explicit.completed"),
        "implicit_feedback_completed": types.count("memory.feedback.implicit.completed"),
        "dreaming_threshold_reached": types.count("memory.dreaming.threshold_reached"),
        "dreaming_started": types.count("memory.dreaming.started"),
        "dreaming_completed": types.count("memory.dreaming.completed"),
        "dreaming_no_action": types.count("memory.dreaming.no_action"),
        "dreaming_failed": types.count("memory.dreaming.failed"),
        "memory_search_diagnostics": len(diagnostics),
        "memory_record_corrupt": sum(
            diagnostic.get("code") == "memory_record_corrupt" for diagnostic in diagnostics
        ),
    }


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


__all__ = [
    "HistoricalTraceSink",
    "LocomoBenchmarkConfig",
    "LocomoBenchmarkRunner",
    "LocomoSelection",
    "load_locomo_selection",
    "render_session_transcript",
    "render_session_prompt",
]
