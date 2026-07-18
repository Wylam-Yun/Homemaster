from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.coworker_demo.verify_run_bundle import verify_presentation_bundle


def test_independent_verifier_rejects_missing_tool_completion(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    path = run_root / "presentation/events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "sequence": 1,
                "event_id": "presentation-1",
                "tool_call_id": "call-1",
                "action_id": "action-1",
                "status": "running",
                "task": {"source_text": "locked SOP", "source_sha256": "bad"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    failures = verify_presentation_bundle(run_root)

    assert "missing_terminal_event:call-1" in failures
    assert "sop_source_hash_mismatch:presentation-1" in failures


def test_independent_verifier_accepts_correlated_locked_sop_events(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    path = run_root / "presentation/events.jsonl"
    path.parent.mkdir(parents=True)
    source_text = "locked SOP"
    source_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    events = [
        {
            "sequence": 1,
            "event_id": "presentation-1",
            "tool_call_id": "call-1",
            "action_id": "action-1",
            "status": "running",
            "task": {"source_text": source_text, "source_sha256": source_sha256},
        },
        {
            "sequence": 2,
            "event_id": "presentation-2",
            "tool_call_id": "call-1",
            "action_id": "action-1",
            "status": "succeeded",
            "task": {"source_text": source_text, "source_sha256": source_sha256},
        },
    ]
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    assert verify_presentation_bundle(run_root) == []
