"""Regression tests for boundary defects found during final acceptance.

Each test pins a typed-failure contract at a seam where an untrusted or
exceptional input previously leaked a raw exception.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from homemaster.agent.messages import ToolCall
from homemaster.cli.client import ApiError, HomeServerClient
from homemaster.config import MemoryConfig
from homemaster.memory.file_store import FileMemoryError, FileMemoryStore
from homemaster.permissions.config import PermissionMode, PermissionSettingsConfig
from homemaster.permissions.models import PermissionStorageUnavailable
from homemaster.permissions.policy import PermissionChecker
from homemaster.permissions.store import PermissionStore
from homemaster.tools import FunctionTool, ToolRegistry
from homemaster.tools.contracts import (
    ToolExecutionResult,
    ToolExecutionStatus,
)
from homemaster.tools.executor import PermissionDecision, ToolExecutor
from tests.homemaster.tools.test_support import ToolExecutionContext, ToolResult


def _echo_tool(**kw: Any) -> FunctionTool:
    return FunctionTool(
        name="echo",
        description="Echo exact input.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        execute=kw.pop(
            "execute",
            lambda arguments, context: ToolResult(str(arguments["value"])),
        ),
        **kw,
    )


def _executor(*tools: FunctionTool, **kw: Any) -> ToolExecutor:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return ToolExecutor(registry, **kw)


# --------------------------------------------------------------- tool seams


@pytest.mark.asyncio
async def test_unknown_tool_name_with_nul_byte_returns_typed_failure(
    tmp_path: Path,
) -> None:
    """A NUL byte in the tool name must produce a typed INVALID result —
    the error-message contract itself must not reject the failure text."""
    executor = _executor(_echo_tool())
    result = await executor.execute(
        ToolCall(id="c1", name="bad\x00name", arguments={}),
        ToolExecutionContext(tmp_path),
    )
    assert result.status is ToolExecutionStatus.INVALID
    assert result.error is not None and result.error.code == "unknown_tool"


@pytest.mark.asyncio
async def test_permission_checker_exception_is_typed_failure(tmp_path: Path) -> None:
    """An unexpected evaluate_tool exception fails closed as a typed error —
    the tool does not run and no raw exception escapes."""

    class Exploding:
        def evaluate_tool(self, **kw: Any) -> PermissionDecision:
            raise RuntimeError("policy engine exploded")

    executor = _executor(_echo_tool(), permission_checker=Exploding())
    result = await executor.execute(
        ToolCall(id="c1", name="echo", arguments={"value": "x"}),
        ToolExecutionContext(tmp_path),
    )
    assert result.is_error
    assert result.error is not None
    assert result.error.code == "permission_check_error"


@pytest.mark.asyncio
async def test_execute_many_propagate_exception_is_not_muted(tmp_path: Path) -> None:
    """_hm_propagate lifecycle exceptions (generation fence, recall deadline)
    must surface raw even via execute_many — converting them to a tool_error
    would let a doomed run continue."""

    class Fence(Exception):
        _hm_propagate = True

    def boom(
        arguments: Mapping[str, Any], context: Any
    ) -> ToolExecutionResult:
        raise Fence("stale generation fence")

    fenced = FunctionTool(
        name="fenced",
        description="fences",
        input_schema={"type": "object"},
        execute=boom,
        read_only=False,
    )
    executor = _executor(_echo_tool(read_only=True), fenced)
    ctx = ToolExecutionContext(tmp_path)
    with pytest.raises(Fence):
        await executor.execute_many(
            [
                (ToolCall(id="c1", name="echo", arguments={"value": "x"}), ctx),
                (ToolCall(id="c2", name="fenced", arguments={}), ctx),
            ]
        )


# ------------------------------------------------------------- permissions


def _checker_ctx(tmp_path: Path) -> Any:
    return ToolExecutionContext(tmp_path)


def test_evaluate_tool_path_with_nul_byte_fails_closed(tmp_path: Path) -> None:
    """A path argument containing NUL must not crash the policy layer; it
    can never match an allow rule, so it resolves to a denial."""
    checker = PermissionChecker(
        PermissionSettingsConfig(mode=PermissionMode.FULL_AUTO)
    )
    decision = checker.evaluate_tool(
        tool_name="file_read",
        is_read_only=True,
        required_capabilities=(),
        arguments={"path": "bad\x00path"},
        context=_checker_ctx(tmp_path),
    )
    assert isinstance(decision.allowed, bool)
    assert not decision.allowed


def test_store_open_on_unwritable_path_raises_typed(tmp_path: Path) -> None:
    """A store path under a regular file (not a dir) cannot host sqlite —
    the failure must be typed PermissionStorageUnavailable, not raw OSError."""
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    with pytest.raises(PermissionStorageUnavailable):
        PermissionStore(blocker / "db.sqlite3")


# ------------------------------------------------------------- thin client


def _client(
    handler: Callable[[httpx.Request], httpx.Response], **kw: Any
) -> HomeServerClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    defaults: dict[str, Any] = dict(backoff_base_s=0.01, jitter=lambda: 0.0)
    defaults.update(kw)
    return HomeServerClient("http://testserver", http_client=http, **defaults)


@pytest.mark.asyncio
async def test_malformed_json_success_response_is_typed() -> None:
    """A 2xx with a broken JSON body must surface as a typed ApiError,
    not a raw JSONDecodeError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{not valid json")

    client = _client(handler)
    try:
        with pytest.raises(ApiError) as exc_info:
            await client.meta()
        assert exc_info.value.code == "invalid_response"
    finally:
        await client.aclose()


# ----------------------------------------------------------------- memory


def test_start_on_blocked_data_root_raises_typed(tmp_path: Path) -> None:
    """FileMemoryStore.start() on a root blocked by a regular file must raise
    FileMemoryError, matching the typed contract used by every other method."""
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    config = MemoryConfig(mode="files", data_root=blocker / "mem")
    store = FileMemoryStore(config)
    with pytest.raises(FileMemoryError):
        store.start()


def _decision_for(arguments: dict[str, Any], tmp_path: Path, **kw: Any) -> Any:
    settings = PermissionSettingsConfig(
        mode=PermissionMode.FULL_AUTO, **kw
    )
    return PermissionChecker(settings).evaluate_tool(
        tool_name="terminal",
        is_read_only=False,
        required_capabilities=(),
        arguments=arguments,
        context=ToolExecutionContext(tmp_path),
    )


def test_command_string_tokenizing_reaches_sensitive_paths(tmp_path: Path) -> None:
    """``cat ~/.ssh/id_rsa`` as a shell ``command`` must not bypass path
    rules — the command argument is free text, so tokens are scanned."""
    home_ssh = str(Path.home() / ".ssh" / "id_rsa")
    decision = _decision_for({"command": f"cat {home_ssh}"}, tmp_path)
    assert not decision.allowed
    assert "protected pattern" in (decision.reason or "")


def test_command_shell_metasyntax_still_scanned(tmp_path: Path) -> None:
    """Quoting and redirections cannot launder a sensitive path."""
    home_ssh = str(Path.home() / ".ssh" / "id_rsa")
    for command in (
        f'cat "{home_ssh}"',
        f"cat < {home_ssh}",
        f"tee {home_ssh} < /dev/null",
    ):
        decision = _decision_for({"command": command}, tmp_path)
        assert not decision.allowed, command


def test_env_and_shadow_paths_are_sensitive(tmp_path: Path) -> None:
    """``.env``/``/etc/shadow``-class credential files are protected."""
    assert not _decision_for({"path": ".env"}, tmp_path).allowed
    assert not _decision_for({"path": "/etc/shadow"}, tmp_path).allowed


def test_case_variant_sensitive_path_denied(tmp_path: Path) -> None:
    """macOS APFS resolves ``~/.SSH/ID_RSA`` to the real key file — deny it."""
    variant = str(Path.home() / ".SSH" / "ID_RSA")
    decision = _decision_for({"path": variant}, tmp_path)
    assert not decision.allowed


def test_user_deny_rule_matches_unresolved_path_form(tmp_path: Path) -> None:
    """A deny rule for ``/etc/*`` must still match on macOS where ``/etc``
    canonicalizes to ``/private/etc`` — match the unresolved form too."""
    from homemaster.permissions.config import PathRuleConfig

    decision = _decision_for(
        {"path": "/etc/passwd"},
        tmp_path,
        path_rules=(PathRuleConfig(pattern="/etc/*", allow=False),),
    )
    assert not decision.allowed


def test_broadened_path_argument_names_are_scanned(tmp_path: Path) -> None:
    """``target``/``source``/``dir``-style argument names are path-scanned,
    not just the original allowlist."""
    home_ssh = str(Path.home() / ".ssh" / "id_rsa")
    for key in ("target", "source", "dir", "file"):
        decision = _decision_for({key: home_ssh}, tmp_path)
        assert not decision.allowed, key


def test_ordinary_command_and_paths_still_allowed(tmp_path: Path) -> None:
    """Regression guard: ordinary commands and benign paths are unaffected."""
    assert _decision_for({"command": "echo hello"}, tmp_path).allowed
    assert _decision_for({"path": "notes.txt"}, tmp_path).allowed
