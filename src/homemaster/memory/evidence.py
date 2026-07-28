"""Persistent, run-bound evidence ledger for memory mutations."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

EvidenceKind = Literal["user_statement", "environment_observation"]


class MemoryEvidenceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class MemoryEvidence:
    ref: str
    provenance_seq: int
    kind: EvidenceKind
    tenant_id: str
    session_id: str
    run_id: str
    turn_id: str
    tool_call_id: str | None


class MemoryEvidenceLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None

    def start(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS memory_evidence (
                provenance_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                ref TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, tenant_id TEXT NOT NULL,
                session_id TEXT NOT NULL, run_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                tool_call_id TEXT, status TEXT NOT NULL, verification TEXT NOT NULL
            )"""
        )
        self._connection.commit()
        self.path.chmod(0o600)

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def register(
        self,
        *,
        kind: EvidenceKind,
        tenant_id: str,
        session_id: str,
        run_id: str,
        turn_id: str,
        tool_call_id: str | None = None,
        status: str = "success",
        verification: str = "passed",
    ) -> MemoryEvidence:
        if status != "success" or verification not in {"passed", "read_observation"}:
            raise MemoryEvidenceError("memory_evidence_invalid", "evidence is not successful")
        ref = f"memory-evidence-{uuid.uuid4().hex}"
        with self._lock:
            connection = self._require_connection()
            cursor = connection.execute(
                """INSERT INTO memory_evidence
                (ref, kind, tenant_id, session_id, run_id, turn_id,
                 tool_call_id, status, verification)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ref,
                    kind,
                    tenant_id,
                    session_id,
                    run_id,
                    turn_id,
                    tool_call_id,
                    status,
                    verification,
                ),
            )
            connection.commit()
            sequence = int(cursor.lastrowid)
        return MemoryEvidence(
            ref, sequence, kind, tenant_id, session_id, run_id, turn_id, tool_call_id
        )

    def validate(
        self,
        refs: Sequence[str],
        *,
        expected_kind: EvidenceKind,
        tenant_id: str,
        session_id: str,
        run_id: str,
        turn_id: str | None = None,
    ) -> tuple[MemoryEvidence, ...]:
        if not refs:
            raise MemoryEvidenceError("memory_evidence_missing", "evidence refs are required")
        evidence = tuple(self._get(ref) for ref in refs)
        if any(
            item.kind != expected_kind
            or item.tenant_id != tenant_id
            or item.session_id != session_id
            or item.run_id != run_id
            or (turn_id is not None and item.turn_id != turn_id)
            for item in evidence
        ):
            raise MemoryEvidenceError("memory_evidence_invalid", "evidence scope or kind mismatch")
        return evidence

    def _get(self, ref: str) -> MemoryEvidence:
        row = (
            self._require_connection()
            .execute(
                """SELECT ref, provenance_seq, kind, tenant_id, session_id, run_id,
                   turn_id, tool_call_id
            FROM memory_evidence WHERE ref = ?""",
                (ref,),
            )
            .fetchone()
        )
        if row is None:
            raise MemoryEvidenceError("memory_evidence_invalid", "unknown evidence ref")
        return MemoryEvidence(*row)

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("memory evidence ledger is not started")
        return self._connection


__all__ = ["MemoryEvidence", "MemoryEvidenceError", "MemoryEvidenceLedger"]
