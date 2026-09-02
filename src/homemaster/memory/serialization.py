"""Deterministic structured-memory text, JSON, index, and identity derivation."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from homemaster.memory.models import FactRecord, MemoryRecord


@dataclass(frozen=True)
class SerializedMemory:
    text: str
    record_json: str
    dedupe_key: str
    metadata: dict[str, str | int]


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def normalize_url(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold()
    port = parsed.port
    if (
        port is not None
        and not (parsed.scheme == "http" and port == 80)
        and not (parsed.scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.casefold(), host, path, parsed.query, ""))


def serialize_record(record: MemoryRecord, *, provenance_seq: int) -> SerializedMemory:
    record_json = json.dumps(
        record.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    extra_meta: dict[str, str | int] = {}
    if isinstance(record, FactRecord):
        identity = record.subject.id or normalize_text(record.subject.name)
        identity_kind = "id" if record.subject.id else "name"
        material = f"fact\0{record.subject.type}\0{identity_kind}\0{identity}\0{record.predicate}"
        value = json.dumps(record.value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        text = f"{record.subject.name} 的 {record.predicate} 是 {value}"
        indexes: dict[str, str | int] = {
            "subject_type": record.subject.type,
            "subject_name_normalized": normalize_text(record.subject.name),
            "predicate": record.predicate,
        }
        if record.subject.id:
            indexes["subject_id"] = record.subject.id
        extra_meta = {"schema_version": record.schema_version, "source": record.source}
    else:
        identity = record.sop_id or record.name
        material = f"procedure\0{normalize_text(identity)}\0{normalize_text(record.name)}"
        step_summary = "；".join(
            f"{step.order}.{step.action}:"
            + json.dumps(
                _outbound_target(step.target.model_dump(mode="json")),
                ensure_ascii=False,
                sort_keys=True,
            )
            for step in record.steps
        )
        text = f"流程 {record.name}，入口 {record.entry.page_name}，步骤 {step_summary}"
        indexes = {
            "procedure_name_normalized": normalize_text(record.name),
            "entry_page_normalized": normalize_text(record.entry.page_name),
        }
        if record.sop_id:
            indexes["sop_id_normalized"] = normalize_text(record.sop_id)
    dedupe_key = hashlib.sha256(material.encode("utf-8")).hexdigest()
    metadata: dict[str, str | int] = {
        "memory_type": record.memory_type,
        "dedupe_key": dedupe_key,
        "record_json": record_json,
        "provenance_seq": provenance_seq,
        **indexes,
        **extra_meta,
    }
    return SerializedMemory(text, record_json, dedupe_key, metadata)


def _outbound_target(value: dict[str, Any]) -> dict[str, Any]:
    """Project a procedure target to retrieval semantics, never executed input values."""

    forbidden = {"value", "input", "input_value", "password", "secret", "token"}

    def project(item: Any, key: str | None = None) -> Any:
        if key is not None and normalize_text(key) in forbidden:
            return None
        if isinstance(item, dict):
            return {
                str(child_key): projected
                for child_key, child_value in sorted(item.items(), key=lambda pair: str(pair[0]))
                if (projected := project(child_value, str(child_key))) is not None
            }
        if isinstance(item, list | tuple):
            return [projected for child in item if (projected := project(child)) is not None]
        if isinstance(item, str) and key is not None and normalize_text(key) in {"url", "href"}:
            parsed = urlsplit(item)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                return parsed._replace(query="", fragment="").geturl()
        return item

    return project(value)


__all__ = ["SerializedMemory", "normalize_text", "normalize_url", "serialize_record"]
