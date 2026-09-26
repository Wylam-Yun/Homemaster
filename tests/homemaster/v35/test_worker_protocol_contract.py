"""Phase 0 checks for the versioned ALFWorld worker protocol."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "protocols" / "alfworld-v1.schema.json"


def test_alfworld_protocol_schema_is_versioned_and_typed() -> None:
    assert SCHEMA_PATH.is_file(), "the versioned ALFWorld protocol schema is missing"
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$id"] == "homemaster-alfworld-v1"
    assert schema["type"] == "object"
    required = set(schema["required"])
    assert {"protocol", "request_id", "operation", "payload"} <= required


def test_worker_protocol_has_explicit_operation_enum() -> None:
    assert SCHEMA_PATH.is_file(), "the versioned ALFWorld protocol schema is missing"
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    properties = schema["properties"]
    assert set(properties["operation"]["enum"]) == {
        "reset",
        "set_task",
        "observe",
        "act",
        "close",
    }


def test_worker_launcher_does_not_inject_main_environment() -> None:
    launcher = (REPO_ROOT / "scripts" / "homemaster").read_text(encoding="utf-8")
    forbidden = ("formal_site", "mindmemos_source", "export PYTHONPATH")
    offenders = [term for term in forbidden if term in launcher]
    assert offenders == [], f"worker launcher still mixes environments: {offenders}"
