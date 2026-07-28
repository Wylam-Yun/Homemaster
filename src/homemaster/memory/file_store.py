"""Private, atomic Markdown stores for HomeMaster's curated memories."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal

from homemaster.config import MemoryConfig

ENTRY_DELIMITER = "\n§\n"
MemoryTarget = Literal["user", "memory"]
MemoryAction = Literal["add", "update", "delete"]


class FileMemoryError(RuntimeError):
    """A stable file-memory failure suitable for tool error mapping."""

    def __init__(self, code: str, message: str, **details: object) -> None:
        self.code = code
        self.details = details
        super().__init__(message)


@dataclass(frozen=True)
class FileMemoryOperation:
    action: MemoryAction
    content: str | None = None
    match: str | None = None


@dataclass(frozen=True)
class FileMemoryState:
    target: MemoryTarget
    path: Path
    entries: tuple[str, ...]
    usage: int
    limit: int
    blocked_entries: tuple[int, ...] = ()


class ThreatScanner:
    """Data-driven scanner for prompt injection, credentials, and exfiltration text."""

    def __init__(self, patterns: Sequence[re.Pattern[str]]) -> None:
        self._patterns = tuple(patterns)

    @classmethod
    def from_package_data(cls) -> ThreatScanner:
        resource = files("homemaster.memory").joinpath("threat_patterns.json")
        payload = json.loads(resource.read_text(encoding="utf-8"))
        return cls([re.compile(item["pattern"], re.IGNORECASE) for item in payload["patterns"]])

    def blocked(self, content: str) -> bool:
        return any(pattern.search(content) for pattern in self._patterns)


class FileMemoryStore:
    """Own and atomically mutate SOUL/USER/MEMORY Markdown files."""

    def __init__(self, config: MemoryConfig, *, scanner: ThreatScanner | None = None) -> None:
        self.config = config
        self._scanner = scanner or ThreatScanner.from_package_data()
        self._lock_path = config.root / ".memory.lock"

    def start(self) -> None:
        self.config.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.config.root, 0o700)
        with self._exclusive_lock():
            self._create_if_missing(self.config.soul_path, self._soul_template())
            self._create_if_missing(self.config.user_path, "")
            self._create_if_missing(self.config.memory_path, "")

    def close(self) -> None:
        """File store owns no persistent descriptor between calls."""

    def read(self, target: MemoryTarget) -> FileMemoryState:
        path, limit = self._target(target)
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise FileMemoryError("memory_read_failed", f"failed to read {target}") from exc
        entries = self._parse(raw)
        blocked = tuple(
            index for index, entry in enumerate(entries) if self._scanner.blocked(entry)
        )
        return FileMemoryState(target, path, entries, len(raw), limit, blocked)

    def read_soul_for_prompt(self) -> str:
        try:
            content = self.config.soul_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise FileMemoryError("memory_read_failed", "failed to read soul") from exc
        return "[BLOCKED: unsafe SOUL.md content]" if self._scanner.blocked(content) else content

    def entries_for_prompt(self, target: MemoryTarget) -> tuple[str, ...]:
        state = self.read(target)
        blocked = set(state.blocked_entries)
        return tuple(
            "[BLOCKED: unsafe memory entry]" if index in blocked else entry
            for index, entry in enumerate(state.entries)
        )

    def apply(
        self, target: MemoryTarget, operations: Sequence[FileMemoryOperation]
    ) -> FileMemoryState:
        if not operations:
            raise FileMemoryError("memory_invalid_input", "operations must not be empty")
        path, limit = self._target(target)
        with self._exclusive_lock():
            try:
                before = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise FileMemoryError("memory_read_failed", f"failed to read {target}") from exc
            try:
                entries = list(self._parse(before))
            except FileMemoryError:
                self._backup(path)
                raise
            if self._serialize(entries) != before:
                self._backup(path)
                raise FileMemoryError("memory_external_drift", "memory file cannot round-trip")
            for operation in operations:
                self._apply_one(entries, operation)
            after = self._serialize(entries)
            if len(after) > limit:
                raise FileMemoryError(
                    "memory_capacity_exceeded",
                    f"{target} memory exceeds its character limit",
                    current_entries=tuple(entries),
                    usage=len(after),
                    limit=limit,
                )
            if after != before:
                self._atomic_write(path, after)
            verified = path.read_text(encoding="utf-8")
            if verified != after:
                raise FileMemoryError("memory_outcome_unknown", "terminal file verification failed")
        return self.read(target)

    def _apply_one(self, entries: list[str], operation: FileMemoryOperation) -> None:
        if operation.action == "add":
            content = self._canonical_content(operation.content)
            if content not in entries:
                entries.append(content)
            return
        match = self._canonical_match(operation.match)
        matches = [index for index, entry in enumerate(entries) if match in entry]
        if not matches:
            raise FileMemoryError("memory_match_not_found", "no entry matched")
        if len(matches) != 1:
            raise FileMemoryError("memory_match_ambiguous", "more than one entry matched")
        index = matches[0]
        if operation.action == "delete":
            entries.pop(index)
            return
        if operation.action == "update":
            content = self._canonical_content(operation.content)
            if content in entries and entries[index] != content:
                raise FileMemoryError("memory_conflict", "updated entry duplicates another entry")
            entries[index] = content
            return
        raise FileMemoryError("memory_invalid_input", "unsupported memory action")

    def _canonical_content(self, value: str | None) -> str:
        if value is None:
            raise FileMemoryError("memory_invalid_input", "content is required")
        content = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not content:
            raise FileMemoryError("memory_invalid_input", "content must not be empty")
        if ENTRY_DELIMITER in content:
            raise FileMemoryError("memory_invalid_input", "content contains the entry delimiter")
        if any(ord(character) < 32 and character not in "\n\t" for character in content):
            raise FileMemoryError("memory_invalid_input", "content contains a control character")
        if self._scanner.blocked(content):
            raise FileMemoryError("memory_content_blocked", "unsafe memory content was blocked")
        return content

    @staticmethod
    def _canonical_match(value: str | None) -> str:
        match = (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not match:
            raise FileMemoryError("memory_invalid_input", "match is required")
        return match

    @staticmethod
    def _parse(raw: str) -> tuple[str, ...]:
        if not raw:
            return ()
        entries = tuple(raw.split(ENTRY_DELIMITER))
        if any(not entry for entry in entries):
            raise FileMemoryError("memory_external_drift", "memory file has an empty entry")
        return entries

    @staticmethod
    def _serialize(entries: Sequence[str]) -> str:
        return ENTRY_DELIMITER.join(entries)

    def _target(self, target: MemoryTarget) -> tuple[Path, int]:
        if target == "user":
            return self.config.user_path, self.config.user_char_limit
        if target == "memory":
            return self.config.memory_path, self.config.memory_char_limit
        raise FileMemoryError("memory_invalid_input", "invalid memory target")

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self.config.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self._lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    @staticmethod
    def _create_if_missing(path: Path, content: str) -> None:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC, 0o600)
        except FileExistsError:
            os.chmod(path, 0o600)
            return
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(temporary)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _backup(path: Path) -> None:
        backup = path.with_name(f".{path.name}.drift-backup")
        shutil.copyfile(path, backup)
        os.chmod(backup, 0o600)

    @staticmethod
    def _soul_template() -> str:
        return files("homemaster.prompts").joinpath("soul.md").read_text(encoding="utf-8")


__all__ = [
    "ENTRY_DELIMITER",
    "FileMemoryError",
    "FileMemoryOperation",
    "FileMemoryState",
    "FileMemoryStore",
    "ThreatScanner",
]
