from __future__ import annotations

import json
from pathlib import Path

from homemaster.benchmarking.locomo.runner import (
    HistoricalTraceSink,
    load_locomo_selection,
    render_session_prompt,
)
from homemaster.events import RuntimeEvent


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
    assert (
        persisted["payload"]["user_text"]
        == "benchmark instruction and wrapped transcript"
    )
    finalizer_event = json.loads(sink.finalizer_path.read_text(encoding="utf-8"))
    assert finalizer_event["timestamp"] == "2023-05-08T13:56:00+00:00"
    assert finalizer_event["payload"]["user_text"] == "Caroline: original conversation"
