"""Shared state and helpers for the embedded MindMemOS runtime mixins."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL as NAMESPACE_URL
from uuid import uuid4 as uuid4
from uuid import uuid5 as uuid5

from homemaster.memory.models import MEMORY_RECORD_ADAPTER, MemoryRecord
from homemaster.memory.serialization import serialize_record as serialize_record

_SCHEMA_EPISODE_MAX_BYTES = 3 * 1024 * 1024

_TYPED_RECORD: ContextVar[dict[str, Any] | None] = ContextVar(
    "homemaster_mindmemos_typed_record",
    default=None,
)
_FEEDBACK_PROVENANCE_SEQ: ContextVar[int | None] = ContextVar(
    "homemaster_feedback_provenance_seq", default=None
)


@dataclass(frozen=True)
class RecordedAddResult:
    add_record_id: str
    result: Any


def _updated_schema_metadata(
    current: Mapping[str, Any] | None,
    replacement: Mapping[str, Any],
) -> dict[str, Any]:
    """Replace HomeMaster request metadata while preserving native schema bookkeeping."""

    merged = dict(current or {})
    request_metadata = merged.get("request_metadata")
    if not isinstance(request_metadata, Mapping):
        merged["request_metadata"] = dict(replacement)
        return merged
    request_copy = dict(request_metadata)
    record_metadata = request_copy.get("record_metadata")
    if isinstance(record_metadata, Sequence) and not isinstance(record_metadata, (str, bytes)):
        items = [dict(item) for item in record_metadata if isinstance(item, Mapping)]
        replaced = False
        for index, item in enumerate(items):
            if "record_json" in item:
                items[index] = {**item, **replacement}
                replaced = True
                break
        if not replaced:
            items.append(dict(replacement))
        request_copy["record_metadata"] = items
    else:
        request_copy.update(replacement)
    merged["request_metadata"] = request_copy
    return merged


def _typed_entity_generation(record: dict[str, Any], prompt: str) -> dict[str, Any]:
    """Project one validated HomeMaster record into the native schema deterministically."""

    date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", prompt)
    memory_date = date_match.group(1) if date_match else "1970-01-01"
    memory_type = record.get("memory_type")
    if memory_type == "fact":
        subject = record.get("subject")
        predicate = record.get("predicate")
        if not isinstance(subject, dict) or not isinstance(subject.get("name"), str):
            raise ValueError("typed fact is missing subject.name")
        if not isinstance(predicate, str) or not predicate:
            raise ValueError("typed fact is missing predicate")
        subject_name = subject["name"]
        value_json = json.dumps(
            record.get("value"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        description = f"{subject_name} 的 {predicate} 是 {value_json}"
        return {
            "entities": [
                {
                    "name": f"{subject_name}::{predicate}",
                    "entity_type": "fact",
                    "description": description,
                    "properties": [
                        {
                            "property_name": "fact_value",
                            "value": description,
                            "time": memory_date,
                        }
                    ],
                }
            ],
            "edges": [],
        }
    if memory_type == "procedure":
        name = record.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("typed procedure is missing name")
        value = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {
            "entities": [
                {
                    "name": name,
                    "entity_type": "task_experience",
                    "description": value,
                    "properties": [
                        {
                            "property_name": "task_experience",
                            "value": value,
                            "time": memory_date,
                        }
                    ],
                }
            ],
            "edges": [],
        }
    raise ValueError("typed record memory_type must be fact or procedure")


def _typed_record_from_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not metadata or metadata.get("homemaster_memory_type") not in {"fact", "procedure"}:
        return None
    value = metadata.get("record_json")
    if not isinstance(value, str):
        return None
    try:
        record = json.loads(value)
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def _record_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Find HomeMaster record metadata inside native request metadata shapes."""

    if not isinstance(metadata, Mapping):
        return {}
    if isinstance(metadata.get("record_json"), str):
        return dict(metadata)
    for value in metadata.values():
        if isinstance(value, Mapping):
            if found := _record_metadata(value):
                return found
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                if isinstance(item, Mapping) and (found := _record_metadata(item)):
                    return found
    return {}


def _record_from_raw_memory(raw: Any) -> MemoryRecord | None:
    metadata = getattr(raw, "metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    nested = metadata.get("request_metadata")
    if isinstance(nested, Mapping):
        record_metadata = nested.get("record_metadata")
        if isinstance(record_metadata, Sequence) and not isinstance(
            record_metadata, (str, bytes)
        ):
            metadata = next(
                (
                    item
                    for item in record_metadata
                    if isinstance(item, Mapping) and "record_json" in item
                ),
                nested,
            )
        else:
            metadata = nested
    value = metadata.get("record_json")
    if not isinstance(value, str):
        return None
    try:
        return MEMORY_RECORD_ADAPTER.validate_json(value)
    except ValueError:
        return None
