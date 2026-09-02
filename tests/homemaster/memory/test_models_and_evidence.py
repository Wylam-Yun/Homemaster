"""Structured record and persistent evidence invariants."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from homemaster.memory.evidence import MemoryEvidenceError, MemoryEvidenceLedger
from homemaster.memory.models import FactRecord, ProcedureRecord
from homemaster.memory.serialization import serialize_record


def _fact(**updates: object) -> FactRecord:
    payload: dict[str, object] = {
        "subject": {"type": "object", "name": "苹果"},
        "predicate": "location",
        "value": {"container": "冰箱", "position": "第二层"},
        "source": "environment_observation",
    }
    payload.update(updates)
    return FactRecord.model_validate(payload)


def _procedure(**updates: object) -> ProcedureRecord:
    payload: dict[str, object] = {
        "name": "查询当前告警",
        "sop_id": "OPS-SOP-001",
        "entry": {
            "page_name": "告警取证台",
            "menu_path": ["运维控制台", "告警取证台"],
            "route_hint": "/ops/alarm-query",
        },
        "inputs": [
            {
                "name": "region",
                "description": "查询区域",
                "required": True,
                "binds_from": "data.change_plan[].region.unique_id",
            }
        ],
        "steps": [
            {
                "order": 1,
                "phase": "STEP-001",
                "action": "open_page",
                "target": {"page_name": "告警取证台"},
                "expect": {"visible_text": "告警列表"},
            },
            {
                "order": 2,
                "action": "select",
                "target": {"role": "combobox", "name": "Region"},
                "use_input": "region",
                "expect": {"field": {"label": "Region", "equals": "{region}"}},
            },
        ],
        "success": {"all_of": ["告警列表已按 {region} 加载"]},
    }
    payload.update(updates)
    return ProcedureRecord.model_validate(payload)


def test_fact_serialization_is_deterministic_and_identity_based() -> None:
    first = serialize_record(_fact(), provenance_seq=7)
    second = serialize_record(_fact(), provenance_seq=7)
    changed_value = serialize_record(_fact(value={"container": "餐桌"}), provenance_seq=8)
    changed_subject = serialize_record(
        _fact(subject={"type": "object", "name": "香蕉"}), provenance_seq=9
    )

    assert first == second
    assert first.dedupe_key == changed_value.dedupe_key
    assert first.dedupe_key != changed_subject.dedupe_key
    assert first.metadata["record_json"] == first.record_json
    assert first.metadata["subject_name_normalized"] == "苹果"
    assert first.metadata["provenance_seq"] == 7


def test_stable_subject_id_controls_fact_identity() -> None:
    first = serialize_record(
        _fact(subject={"type": "device", "name": "走廊灯", "id": "device-1"}),
        provenance_seq=1,
    )
    renamed = serialize_record(
        _fact(subject={"type": "device", "name": "门厅灯", "id": "device-1"}),
        provenance_seq=2,
    )
    assert first.dedupe_key == renamed.dedupe_key
    assert first.metadata["subject_id"] == "device-1"


def test_procedure_identity_uses_name_and_omits_host() -> None:
    first = serialize_record(_procedure(), provenance_seq=10)
    same_identity = serialize_record(
        _procedure(
            entry={
                "page_name": "告警取证台",
                "menu_path": ["运维控制台", "告警取证台"],
                "route_hint": "/ops/other",
            }
        ),
        provenance_seq=11,
    )
    other_name = serialize_record(_procedure(name="另一流程"), provenance_seq=12)
    assert "127.0.0.1" not in first.text
    assert "://" not in first.text
    assert "告警取证台" in first.text
    assert first.dedupe_key == same_identity.dedupe_key
    assert first.dedupe_key != other_name.dedupe_key
    assert first.metadata["entry_page_normalized"]
    assert "source" not in first.metadata


@pytest.mark.parametrize(
    "payload",
    [
        {"predicate": "Power State"},
        {"value": None},
        {"subject": {"type": "object", "name": ""}},
        {"extra": "forbidden"},
    ],
)
def test_fact_rejects_invalid_or_unexplained_fields(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _fact(**payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"entry": {"page_name": "告警取证台", "route_hint": "http://127.0.0.1:8002/ops"}},
        {
            "steps": [
                {
                    "order": 2,
                    "action": "click",
                    "target": {"name": "go"},
                    "expect": {"visible_text": "x"},
                }
            ]
        },
        {
            "steps": [
                {
                    "order": 1,
                    "action": "click",
                    "target": {"name": "go", "ref": "e1"},
                    "expect": {"visible_text": "x"},
                }
            ]
        },
        {
            "steps": [
                {
                    "order": 1,
                    "action": "open_page",
                    "target": {"name": "go"},
                    "expect": {"visible_text": "x"},
                }
            ]
        },
        {
            "steps": [
                {
                    "order": 1,
                    "action": "click",
                    "target": {"name": "go"},
                    "use_input": "missing",
                    "expect": {"visible_text": "x"},
                }
            ]
        },
    ],
)
def test_procedure_rejects_unsafe_or_unverifiable_shapes(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _procedure(**payload)


def test_evidence_is_private_persistent_monotonic_and_scope_bound(tmp_path: Path) -> None:
    path = tmp_path / "private" / "evidence.sqlite3"
    ledger = MemoryEvidenceLedger(path)
    ledger.start()
    first = ledger.register(
        kind="user_statement",
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
        turn_id="turn-a",
    )
    ledger.close()

    reopened = MemoryEvidenceLedger(path)
    reopened.start()
    second = reopened.register(
        kind="environment_observation",
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
        turn_id="turn-b",
        tool_call_id="tool-1",
    )
    assert second.provenance_seq > first.provenance_seq
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert reopened.validate(
        [second.ref],
        expected_kind="environment_observation",
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
    ) == (second,)

    for mismatch in (
        {"tenant_id": "tenant-b", "session_id": "session-a", "run_id": "run-a"},
        {"tenant_id": "tenant-a", "session_id": "session-b", "run_id": "run-a"},
        {"tenant_id": "tenant-a", "session_id": "session-a", "run_id": "run-b"},
    ):
        with pytest.raises(MemoryEvidenceError) as caught:
            reopened.validate([second.ref], expected_kind="environment_observation", **mismatch)
        assert caught.value.code == "memory_evidence_invalid"
    reopened.close()


def test_evidence_rejects_missing_unknown_and_failed_results(tmp_path: Path) -> None:
    ledger = MemoryEvidenceLedger(tmp_path / "evidence.sqlite3")
    ledger.start()
    with pytest.raises(MemoryEvidenceError) as missing:
        ledger.validate(
            [], expected_kind="user_statement", tenant_id="t", session_id="s", run_id="r"
        )
    assert missing.value.code == "memory_evidence_missing"
    with pytest.raises(MemoryEvidenceError) as unknown:
        ledger.validate(
            ["forged"],
            expected_kind="user_statement",
            tenant_id="t",
            session_id="s",
            run_id="r",
        )
    assert unknown.value.code == "memory_evidence_invalid"
    with pytest.raises(MemoryEvidenceError) as failed:
        ledger.register(
            kind="environment_observation",
            tenant_id="t",
            session_id="s",
            run_id="r",
            turn_id="turn",
            status="failure",
            verification="failed",
        )
    assert failed.value.code == "memory_evidence_invalid"
    ledger.close()
