"""Exact allowlist for the case_02 black-screen verification command."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path


class CommandPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedCommand:
    original: str
    tokens: tuple[str, ...]


class CommandPolicy:
    sandbox_path = Path("/opt/app/service_layer/component/config/extension_item_mapping.json")

    def __init__(self, tenant_id: str, item_code: str) -> None:
        self.tenant_id = tenant_id
        self.item_code = item_code
        self.expected_tokens = (
            "grep",
            "-A",
            "3",
            f"{tenant_id}:{item_code}",
            str(self.sandbox_path),
        )

    @property
    def exact_command(self) -> str:
        return f'grep -A 3 "{self.tenant_id}:{self.item_code}" {self.sandbox_path}'

    def parse(self, command: str) -> ParsedCommand:
        try:
            tokens = tuple(shlex.split(command, posix=True))
        except ValueError as exc:
            raise CommandPolicyError(f"invalid shell syntax: {exc}") from exc
        if tokens != self.expected_tokens:
            raise CommandPolicyError("only the locked grep -A 3 command is allowed")
        return ParsedCommand(original=command, tokens=tokens)
