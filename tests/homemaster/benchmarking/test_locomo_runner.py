from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from homemaster.agent.messages import ContentBlock, UserMessage
from homemaster.application import ApplicationSession, RunResult, RunStatus
from homemaster.benchmarking.locomo.runner import (
    HistoricalTraceSink,
    LocomoBenchmarkConfig,
    LocomoBenchmarkRunner,
    load_locomo_selection,
    render_session_prompt,
)
from homemaster.events import RuntimeEvent
from homemaster.memory.archive_service import ArchiveService
from homemaster.memory.memory_jobs import default_executors
from homemaster.memory.memory_work_store import SQLiteMemoryWorkStore
from homemaster.memory.memory_workers import MemoryWorkerPool
from homemaster.memory.mindmemos_client import (
    AddReceipt,
    DreamingReceipt,
    OperationStatus,
)
from homemaster.memory.session_trace_store import SessionTraceStore


def test_locomo_finalizes_source_sessions_but_not_qa_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_file = tmp_path / "locomo.json"
    data_file.write_text(
        json.dumps(
            [
                {
                    "sample_id": "conv-test",
                    "conversation": {
                        "speaker_a": "Caroline",
                        "speaker_b": "Melanie",
                        "session_1_date_time": "1:56 pm on 8 May, 2023",
                        "session_1": [{"dia_id": "D1:1", "speaker": "Caroline", "text": "Hello."}],
                    },
                    "qa": [
                        {
                            "question": "What happened?",
                            "evidence": ["D1:1"],
                            "category": 1,
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    admitted: list[tuple[str, str]] = []
    run_session_ids: list[str] = []

    # Real V3.8 durable surfaces; only the MindMemOS edge and raw verifier
    # are faked (no live service needed for this unit test).
    store = SQLiteMemoryWorkStore(tmp_path / "memory_work.db")
    store.open()
    trace_store = SessionTraceStore(store)
    archive_service = ArchiveService(store)

    class FakeMindMemOSClient:
        async def health(self) -> bool:
            return True

        async def aclose(self) -> None:
            return None

        async def add_turn(self, *, operation_id, session_id, turn_id, dialogue, metadata=None):
            return AddReceipt(
                operation_id=operation_id,
                add_record_id=f"ar-{operation_id}",
                memory_ids=(f"mem-{operation_id}",),
                status="completed",
                request_id="req-1",
            )

        async def operation_status(self, operation_id):
            return OperationStatus(
                operation_id=operation_id,
                kind="add",
                status="completed",
                result={
                    "add_record_id": f"ar-{operation_id}",
                    "memory_ids": [f"mem-{operation_id}"],
                },
                error=None,
            )

        async def dreaming(self, *, operation_id, seed_add_record_ids, session_id=None):
            return DreamingReceipt(
                operation_id=operation_id,
                dreaming_record_id=f"dream-{operation_id}",
                produced_memory_ids=(f"dreamed-{operation_id}",),
                actions=({"action": "link", "memory_id": None},),
                status="completed",
                request_id="req-1",
            )

    class FakeVerifier:
        async def memory_exists(self, memory_id) -> bool:
            return True

        async def add_record_exists(self, add_record_id) -> bool:
            return True

        async def memory_ids_exist(self, memory_ids) -> dict[str, bool]:
            return {mid: True for mid in memory_ids}

        async def close(self) -> None:
            return None

    client = FakeMindMemOSClient()
    pool = MemoryWorkerPool(
        store=store,
        client=client,
        executors=default_executors(
            store=store,
            trace=trace_store,
            client=client,
            verifier=FakeVerifier(),
        ),
        owner_id="locomo-test-worker",
    )

    class Application:
        def session(self, session_id: str, *, exit_reason: str) -> ApplicationSession:
            return ApplicationSession(self, session_id, exit_reason)

        def __init__(self) -> None:
            self.session_end_handler = self._admit

        def _admit(self, session_id: str, reason: str) -> object:
            admitted.append((session_id, reason))
            result = archive_service.archive_session(session_id)
            if result.archive_id is None:
                return None
            return SimpleNamespace(job_id=result.archive_id)

        async def run(self, request):
            run_session_ids.append(request.session_id)
            # Mirror the production runtime: one durable terminal turn plus
            # its Add intent, committed atomically inside the run boundary.
            trace_store.begin_turn(
                request.session_id,
                1,
                f"run-{len(run_session_ids)}",
                message_start_index=0,
            )
            trace_store.commit_terminal(
                request.session_id,
                "turn-1",
                status="completed",
                messages=[
                    UserMessage(
                        content=[ContentBlock(type="text", text="benchmark turn")]
                    )
                ],
            )
            return RunResult(
                run_id=f"run-{len(run_session_ids)}",
                session_id=request.session_id or "missing",
                status=RunStatus.REPLIED,
                final_reply="ok",
            )

        async def aclose(self) -> None:
            return None

    application = Application()

    bundle = SimpleNamespace(
        application=application,
        mindmemos_client=client,
        memory_work_store=store,
        archive_service=archive_service,
        memory_worker_pool=pool,
        session_trace=trace_store,
        config=SimpleNamespace(runtime=SimpleNamespace(max_tool_iterations=2)),
    )
    monkeypatch.setattr(
        "homemaster.benchmarking.locomo.runner.compose_application",
        lambda **_kwargs: bundle,
    )

    try:
        summary = LocomoBenchmarkRunner(
            LocomoBenchmarkConfig(
                data_file=data_file,
                sample_id="conv-test",
                focal_speaker="Caroline",
                trace_root=tmp_path / "traces",
                home_config=object(),
                max_source_turns=10,
                qa_probes=1,
                run_id="locomo-test",
            )
        ).run()
    finally:
        store.close()

    assert summary["status"] == "completed"
    assert run_session_ids == ["locomo-test-conv-test-source-1", "locomo-test-conv-test-qa-1"]
    # The archive-admission set contains source sessions only — QA probes stay
    # ordinary single-turn runs (benchmark/eval boundary rule).
    assert admitted == [("locomo-test-conv-test-source-1", "locomo_source_session_end")]

    session_records = [
        json.loads(line)
        for line in Path(summary["artifacts"]["sessions"]).read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    source = next(record for record in session_records if record["record_type"] == "source_session")
    finalization = source["finalization"]
    assert finalization["finalization_status"] == "completed"
    assert finalization["archive_id"] == "archive:locomo-test-conv-test-source-1:0"
    assert finalization["archive_status"] == "archived"
    assert finalization["add_operation_ids"] == [
        "turnadd:locomo-test-conv-test-source-1:turn-1"
    ]
    assert [job["kind"] for job in finalization["operations"]] == ["turn_add"]
    assert finalization["operations"][0]["status"] == "completed"
    assert finalization["operations"][0]["memory_ids"] == [
        "mem-turnadd:locomo-test-conv-test-source-1:turn-1"
    ]
    assert [job["kind"] for job in finalization["archive_jobs"]] == ["archive_dreaming"]
    assert finalization["archive_jobs"][0]["status"] == "completed"
    assert finalization["pending_jobs"] == []

    readback = source["terminal_memory_readback"]
    kinds = {entry["operation"] for entry in readback}
    assert kinds == {"turn_add", "archive_dreaming"}
    assert all(entry["status"] == "completed" for entry in readback)
    assert summary["feature_counts"]["vanilla_add_operations"] == 1


def test_locomo_selection_is_chronological_and_truncates_exactly(tmp_path: Path) -> None:
    session_1 = [
        {"dia_id": f"D1:{index}", "speaker": "Caroline", "text": f"early {index}"}
        for index in range(1, 41)
    ]
    session_2 = [
        {"dia_id": f"D2:{index}", "speaker": "Melanie", "text": f"late {index}"}
        for index in range(1, 31)
    ]
    session_1[2]["blip_caption"] = "a transgender pride flag mural"
    data_file = tmp_path / "locomo.json"
    data_file.write_text(
        json.dumps(
            [
                {
                    "sample_id": "conv-test",
                    "conversation": {
                        "speaker_a": "Caroline",
                        "speaker_b": "Melanie",
                        "session_2_date_time": "1:14 pm on 25 May, 2023",
                        "session_2": session_2,
                        "session_1_date_time": "1:56 pm on 8 May, 2023",
                        "session_1": session_1,
                    },
                    "qa": [
                        {
                            "question": "What happened early?",
                            "evidence": ["D1:3"],
                            "category": 1,
                        },
                        {
                            "question": "What happened after the cutoff?",
                            "evidence": ["D2:20"],
                            "category": 1,
                        },
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    selection = load_locomo_selection(
        data_file,
        sample_id="conv-test",
        focal_speaker="Caroline",
        max_source_turns=50,
        qa_probes=10,
    )

    assert selection.source_turn_count == 50
    assert [session.source_index for session in selection.sessions] == [1, 2]
    assert [len(session.turns) for session in selection.sessions] == [40, 10]
    assert selection.sessions[1].partial is True
    assert [question.question for question in selection.questions] == ["What happened early?"]
    no_questions = load_locomo_selection(
        data_file,
        sample_id="conv-test",
        focal_speaker="Caroline",
        max_source_turns=50,
        qa_probes=0,
    )
    assert no_questions.questions == ()
    prompt = render_session_prompt(selection, selection.sessions[0])
    assert "Historical conversation between Caroline and Melanie" in prompt
    assert "Caroline: early 3" in prompt
    assert "[Image shared by Caroline: a transgender pride flag mural]" in prompt
    assert "1:56 pm on 8 May, 2023" in prompt


def test_historical_trace_replaces_runtime_time_for_bound_source_session(
    tmp_path: Path,
) -> None:
    sink = HistoricalTraceSink(tmp_path)
    sink.bind(
        "source-session",
        "2023-05-08T13:56:00+00:00",
        finalizer_user_text="Caroline: original conversation",
    )

    sink.emit(
        RuntimeEvent(
            type="runtime.turn_started",
            session_id="source-session",
            run_id="run-1",
            turn_index=1,
            payload={"user_text": "benchmark instruction and wrapped transcript"},
        )
    )
    sink.close()

    persisted = json.loads(sink.path.read_text(encoding="utf-8"))
    assert persisted["timestamp"] == "2023-05-08T13:56:00+00:00"
    assert persisted["payload"]["user_text"] == "benchmark instruction and wrapped transcript"
    finalizer_event = json.loads(sink.finalizer_path.read_text(encoding="utf-8"))
    assert finalizer_event["timestamp"] == "2023-05-08T13:56:00+00:00"
    assert finalizer_event["payload"]["user_text"] == "Caroline: original conversation"
