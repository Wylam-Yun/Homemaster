"""Replay LoCoMo conversations through the production HomeMaster application."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from homemaster.application import RunPolicy, RunRequest, RunStatus
from homemaster.application.composition import compose_application
from homemaster.events.event_payloads import trace_event_payload
from homemaster.memory.memory_work_store import (
    JOB_TERMINAL_STATUSES,
    JOB_TURN_ADD,
    ArchiveSnapshot,
    OutboxJob,
)
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
        bundle = compose_application(
            config=self.config.home_config,
            run_label=run_id,
            quiet=True,
            event_sink=trace,
            tool_environment=None,
            memory_tenant_id=_memory_scope_key(selection.focal_speaker),
        )
        if (
            bundle.mindmemos_client is None
            or bundle.memory_work_store is None
            or bundle.archive_service is None
        ):
            trace.close()
            raise RuntimeError("LoCoMo benchmark requires HomeMaster memory to be enabled")
        application = bundle.application
        memory_tenant_id = _memory_scope_key(selection.focal_speaker)
        if bundle.archive_service is None or bundle.memory_worker_pool is None:
            trace.close()
            raise RuntimeError(
                "LoCoMo benchmark requires session archival and the memory worker pool"
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
                session_scope = application.session(
                    session_id, exit_reason="locomo_source_session_end"
                )
                async with session_scope:
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
                if session_scope.receipt is None:
                    raise RuntimeError("source session finalization was not admitted")
                archive_id = getattr(session_scope.receipt, "job_id", None)
                if not archive_id:
                    raise RuntimeError("source session finalization was not admitted")
                finalization = await _finalize_archived_session(
                    bundle,
                    session_id=session_id,
                    archive_id=str(archive_id),
                    deadline_s=_drain_deadline_seconds(self.config),
                )
                record["finalization"] = finalization
                record["terminal_memory_readback"] = await _readback_operations(
                    bundle.memory_work_store,
                    session_id,
                )
                session_records.append(record)
                finalization_records.append(
                    {
                        "record_type": "session_finalization",
                        "source_session": session.source_index,
                        "source_timestamp": session.source_timestamp,
                        **finalization,
                    }
                )
                _append_jsonl(sessions_path, record)
                if finalization["finalization_status"] != "completed":
                    raise RuntimeError(
                        f"Session finalization failed for source session {session.source_index}: "
                        f"{finalization.get('error') or finalization['finalization_status']}"
                    )

            for index, question in enumerate(selection.questions, start=1):
                session_id = f"{run_id}-{selection.sample_id}-qa-{index}"
                result = await application.run(
                    RunRequest(
                        text=(
                            f"Answer this question about {selection.focal_speaker}'s prior "
                            "conversation. Use recalled long-term memory or a memory "
                            "search tool "
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
                    f"{failure}; application close: {close_failure}" if failure else close_failure
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


_DRAIN_POLL_SECONDS = 0.05
_DRAIN_DEADLINE_FACTOR = 5.0


def _drain_deadline_seconds(config: LocomoBenchmarkConfig) -> float:
    """Bounded drain budget: the source run's own deadline scaled up so
    retry/backoff cycles (5-15s ``not_before``) fit comfortably inside it."""
    return _DRAIN_DEADLINE_FACTOR * config.run_deadline_seconds


def _session_outbox_jobs(
    store: Any,
    *,
    session_id: str,
    archive_snapshot: ArchiveSnapshot | None,
) -> list[OutboxJob]:
    """All durable outbox jobs bound to this session: one ``turn_add`` intent
    per terminal turn plus the archive's dreaming intent. The work store has
    no ``jobs_for_session`` query; turns and archives carry the job ids."""
    jobs: list[OutboxJob] = []
    seen: set[str] = set()
    for turn in store.turns_for_session(session_id):
        if not turn.add_job_id or turn.add_job_id in seen:
            continue
        job = store.job(turn.add_job_id)
        if job is not None:
            seen.add(job.job_id)
            jobs.append(job)
    if archive_snapshot is not None:
        dreaming_id = archive_snapshot.dreaming_job_id
        job = (
            store.job(dreaming_id)
            if dreaming_id
            else store.job_by_operation_key(f"dreaming:{archive_snapshot.archive_id}")
        )
        if job is not None and job.job_id not in seen:
            jobs.append(job)
    return jobs


def _outbox_job_dict(job: OutboxJob) -> dict[str, Any]:
    result = dict(job.result or {})
    return {
        "job_id": job.job_id,
        "kind": job.kind,
        "operation_key": job.operation_key,
        "turn_id": job.turn_id,
        "archive_id": job.archive_id,
        "status": job.status,
        "attempts": job.attempts,
        "last_error": job.last_error,
        "add_record_id": result.get("add_record_id"),
        "memory_ids": [
            str(value)
            for value in (result.get("memory_ids") or result.get("produced_memory_ids") or ())
        ],
        "raw_verified": result.get("raw_verified"),
        "missing_ids": [str(value) for value in (result.get("missing_ids") or ())],
        "result": result,
    }


async def _finalize_archived_session(
    bundle: Any,
    *,
    session_id: str,
    archive_id: str,
    deadline_s: float,
) -> dict[str, Any]:
    """V3.8 finalization: drain the durable outbox until every job bound to
    this session (turn_add intents + the archive's dreaming intent) reaches a
    terminal status.

    The worker pool's background loop may already be running; ``run_once()``
    is still called each iteration so the benchmark drives claims
    deterministically (claims are atomic in the store). Terminal turns
    without a persisted add intent cannot self-heal in-process (only startup
    recovery rebuilds them), so they fail fast instead of waiting out the
    deadline.
    """
    store = bundle.memory_work_store
    pool = bundle.memory_worker_pool
    started = time.monotonic()
    deadline = started + max(deadline_s, _DRAIN_POLL_SECONDS)
    timed_out = False
    while True:
        if pool is not None:
            await pool.run_once()
        snapshot = store.archive(archive_id)
        jobs = _session_outbox_jobs(
            store, session_id=session_id, archive_snapshot=snapshot
        )
        missing_intent_turns = [
            turn.turn_id
            for turn in store.turns_for_session(session_id)
            if turn.add_job_id is None
        ]
        pending = [job for job in jobs if job.status not in JOB_TERMINAL_STATUSES]
        if missing_intent_turns or not pending:
            break
        if time.monotonic() >= deadline:
            timed_out = True
            break
        await asyncio.sleep(_DRAIN_POLL_SECONDS)
    return _finalization_record(
        archive_id=archive_id,
        snapshot=snapshot,
        jobs=jobs,
        missing_intent_turns=missing_intent_turns,
        timed_out=timed_out,
        duration_ms=(time.monotonic() - started) * 1000.0,
    )


def _finalization_record(
    *,
    archive_id: str,
    snapshot: ArchiveSnapshot | None,
    jobs: list[OutboxJob],
    missing_intent_turns: list[str],
    timed_out: bool,
    duration_ms: float,
) -> dict[str, Any]:
    failed = [job for job in jobs if job.status in {"failed", "stale"}]
    pending = [job for job in jobs if job.status not in JOB_TERMINAL_STATUSES]
    if snapshot is None:
        # The session-close receipt promised this archive id; its absence means
        # the durable boundary was lost — never report that as success.
        status = "failed"
        error = f"archive snapshot not found in work store: {archive_id}"
    elif missing_intent_turns:
        status = "failed"
        error = f"terminal turns missing add intents: {', '.join(missing_intent_turns)}"
    elif timed_out:
        status = "drain_timeout"
        error = (
            "memory work drain exceeded deadline; pending="
            + str([f"{job.job_id}:{job.status}" for job in pending])
        )
    elif failed:
        status = "failed"
        error = "; ".join(
            f"{job.job_id} {job.status}: {job.last_error or ''}".rstrip() for job in failed
        )
    else:
        status = "completed"
        error = None
    return {
        "finalization_status": status,
        "archive_id": archive_id,
        "archive_status": "archived" if snapshot is not None else "missing",
        "max_event_seq": snapshot.max_event_seq if snapshot is not None else -1,
        "turn_range_start": snapshot.turn_range_start if snapshot is not None else None,
        "turn_range_end": snapshot.turn_range_end if snapshot is not None else None,
        "collected_events": (
            snapshot.max_event_seq + 1
            if snapshot is not None and snapshot.max_event_seq >= 0
            else 0
        ),
        "add_operation_ids": list(snapshot.add_operation_ids) if snapshot else [],
        "operations": [
            _outbox_job_dict(job) for job in jobs if job.kind == JOB_TURN_ADD
        ],
        "archive_jobs": [
            _outbox_job_dict(job) for job in jobs if job.kind != JOB_TURN_ADD
        ],
        "missing_intent_turns": list(missing_intent_turns),
        "pending_jobs": [
            {"job_id": job.job_id, "kind": job.kind, "status": job.status}
            for job in pending
        ],
        "duration_ms": duration_ms,
        "error": error,
    }


async def _readback_operations(
    store: Any,
    session_id: str,
) -> list[dict[str, Any]]:
    """Terminal memory readback from the durable work store.

    Per terminal turn, surfaces the settled ``turn_add`` job outcome — the
    worker's own raw-store verification (``raw_verified``/``missing_ids``) is
    the authoritative readback evidence under V3.8, replacing the embedded
    ``get_raw`` probes. A terminal turn whose add intent or job row is absent
    is a durable-contract breach and raises; non-terminal leftovers are
    recorded so the finalization gate can report the full pending set.
    """
    readback: list[dict[str, Any]] = []
    for turn in store.turns_for_session(session_id):
        job = store.job(turn.add_job_id) if turn.add_job_id else None
        if job is None:
            raise RuntimeError(
                "terminal memory readback failed for "
                f"{session_id}/{turn.turn_id}: add job missing from work store"
            )
        result = dict(job.result or {})
        readback.append(
            {
                "turn_id": turn.turn_id,
                "job_id": job.job_id,
                "operation": job.kind,
                "status": job.status,
                "add_record_id": result.get("add_record_id"),
                "memory_ids": [
                    str(value) for value in (result.get("memory_ids") or ())
                ],
                "raw_verified": result.get("raw_verified"),
                "missing_ids": [
                    str(value) for value in (result.get("missing_ids") or ())
                ],
                "replayed": result.get("replayed"),
                "reconciled": result.get("reconciled"),
                "attempts": job.attempts,
                "last_error": job.last_error,
            }
        )
    for snapshot in store.archives_for_session(session_id):
        dreaming_id = snapshot.dreaming_job_id
        job = (
            store.job(dreaming_id)
            if dreaming_id
            else store.job_by_operation_key(f"dreaming:{snapshot.archive_id}")
        )
        if job is None:
            continue
        result = dict(job.result or {})
        readback.append(
            {
                "turn_id": None,
                "job_id": job.job_id,
                "operation": job.kind,
                "archive_id": snapshot.archive_id,
                "status": job.status,
                "dreaming_record_id": result.get("dreaming_record_id"),
                "memory_ids": [
                    str(value)
                    for value in (result.get("produced_memory_ids") or ())
                ],
                "raw_verified": result.get("raw_verified"),
                "missing_ids": [
                    str(value) for value in (result.get("missing_ids") or ())
                ],
                "actions": list(result.get("actions") or ()),
                "attempts": job.attempts,
                "last_error": job.last_error,
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
