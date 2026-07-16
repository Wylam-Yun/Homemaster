"""Normalize persisted multi-source evidence into externally proven actions."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from case02_openenv.artifacts import canonical_json
from case02_openenv.models import AuditEvent


def normalize_events(events: list[AuditEvent]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    effective: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    known_evidence: set[str] = set()
    for event in events:
        payload = event.model_dump(mode="json")
        reason = None
        if event.run_id == "":
            reason = "missing_run_id"
        elif event.status not in {"succeeded", "accepted"}:
            reason = "external_status_not_success"
        elif event.node_id is None:
            reason = "no_grounded_node"
        elif event.action_id is None and event.node_id not in {"PLAN_CREATED"}:
            reason = "missing_action_id"
        elif event.source == "decision" and not set(event.evidence_refs).issubset(known_evidence):
            reason = "unknown_evidence_ref"
        if reason:
            rejected.append({"event_id": event.event_id, "reason": reason})
        else:
            effective.append(
                {
                    "schema_version": 1,
                    "effective_action_id": f"effective-{event.event_id}",
                    "run_id": event.run_id,
                    "action_id": event.action_id,
                    "tool_name": event.arguments.get("tool_name", event.kind),
                    "stage": event.stage,
                    "node_id": event.node_id,
                    "normalized_arguments": event.arguments,
                    "evidence_refs": [event.event_id, *event.evidence_refs],
                    "raw_event_ids": [event.event_id],
                    "raw_event_hashes": [hashlib.sha256(canonical_json(payload)).hexdigest()],
                    "sequence": event.sequence,
                }
            )
        if event.status in {"accepted", "succeeded"}:
            known_evidence.add(event.event_id)
            if event.source in {"backend", "browser", "terminal", "state"}:
                known_evidence.update(event.evidence_refs)
    return effective, rejected


def write_jsonl(path: Any, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records
        ),
        encoding="utf-8",
    )
