"""HomeMaster Bash tool with process-tree ownership and terminal-state receipts.

Commands run in a separate process group.  The executor therefore owns the
complete process tree on timeout or cancellation instead of leaving an
installer, git process, or shell child behind after the tool call ends.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
from collections.abc import Mapping
from pathlib import Path

from homemaster.tools.contracts import (
    ConcurrencyPolicy,
    ExecutionProof,
    RegisteredTool,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
)
from homemaster.tools.paths import ToolPathError, resolve_context_tool_path

_IMPLEMENTATION_REFERENCE = "homemaster.tools.bash"
_MAX_OUTPUT_CHARS = 12_000
_MAX_CAPTURED_OUTPUT_BYTES = 1_000_000
_PROCESS_GRACE_SECONDS = 2.0


class BashExecutor:
    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        command = _string(arguments, "command")
        preflight_error = _preflight_interactive_command(command)
        if preflight_error is not None:
            return _failure(
                "interactive_command",
                preflight_error,
                attempted=False,
                data={"interactive_required": True},
            )
        try:
            cwd = _resolve_cwd(arguments, context)
        except ToolPathError as exc:
            return _failure("invalid_cwd", str(exc), attempted=False)
        timeout_seconds = _integer(arguments, "timeout_seconds", default=600)
        process: asyncio.subprocess.Process | None = None
        reader: asyncio.Task[tuple[bytearray, bool]] | None = None
        try:
            process = await _create_shell_subprocess(command, cwd)
            reader = asyncio.create_task(_capture_output(process.stdout))
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
            except TimeoutError:
                await _terminate_process_group(process, force=True)
                output, truncated = await _finish_reader(reader, process.pid)
                return _timeout_result(
                    command=command,
                    timeout_seconds=timeout_seconds,
                    cwd=cwd,
                    output=output,
                    output_truncated=truncated,
                    returncode=process.returncode,
                )
            output, truncated = await _finish_reader(reader, process.pid)
        except asyncio.CancelledError:
            if process is not None:
                await _terminate_process_group(process, force=False)
            if reader is not None:
                await _finish_reader(reader, process.pid if process is not None else None)
            raise
        except OSError as exc:
            return _failure(
                "subprocess_start_failed",
                f"Unable to start shell command: {exc}",
                attempted=False,
            )

        assert process is not None
        text = _format_output(output, truncated=truncated)
        data = {
            "command": command,
            "cwd": str(cwd),
            "returncode": process.returncode,
            "timed_out": False,
            "output_truncated": truncated,
        }
        if process.returncode == 0:
            return ToolExecutionResult(
                status=ToolExecutionStatus.SUCCESS,
                text=text,
                data=data,
                backend_attempted=True,
            )
        return _failure(
            "command_failed",
            f"command exited with return code {process.returncode}",
            attempted=True,
            text=text,
            data=data,
        )


async def _create_shell_subprocess(command: str, cwd: Path) -> asyncio.subprocess.Process:
    argv = _shell_argv(command)
    kwargs: dict[str, object] = {
        "cwd": str(cwd),
        "stdin": asyncio.subprocess.DEVNULL,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.STDOUT,
    }
    if os.name == "posix":
        kwargs["start_new_session"] = True
    return await asyncio.create_subprocess_exec(*argv, **kwargs)


def _shell_argv(command: str) -> list[str]:
    bash = shutil.which("bash")
    shell = bash or shutil.which("sh") or os.environ.get("SHELL") or "/bin/sh"
    if os.name == "posix":
        script = shutil.which("script")
        if script is not None:
            return [script, "-qefc", command, "/dev/null"]
    return [shell, "-lc", command]


async def _capture_output(
    stream: asyncio.StreamReader | None,
) -> tuple[bytearray, bool]:
    captured = bytearray()
    truncated = False
    if stream is None:
        return captured, truncated
    while True:
        chunk = await stream.read(65_536)
        if not chunk:
            return captured, truncated
        remaining = _MAX_CAPTURED_OUTPUT_BYTES - len(captured)
        if remaining > 0:
            captured.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True


async def _finish_reader(
    reader: asyncio.Task[tuple[bytearray, bool]],
    process_group_id: int | None,
) -> tuple[bytearray, bool]:
    try:
        return await asyncio.wait_for(asyncio.shield(reader), timeout=_PROCESS_GRACE_SECONDS)
    except TimeoutError:
        if process_group_id is not None:
            _signal_process_group(process_group_id, signal.SIGKILL)
        try:
            return await asyncio.wait_for(asyncio.shield(reader), timeout=_PROCESS_GRACE_SECONDS)
        except TimeoutError:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            return bytearray(), True


async def _terminate_process_group(
    process: asyncio.subprocess.Process,
    *,
    force: bool,
) -> None:
    process_group_id = process.pid
    if os.name == "posix":
        _signal_process_group(process_group_id, signal.SIGKILL if force else signal.SIGTERM)
    elif process.returncode is None:
        if force:
            process.kill()
        else:
            process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=_PROCESS_GRACE_SECONDS)
    except TimeoutError:
        if os.name == "posix":
            _signal_process_group(process_group_id, signal.SIGKILL)
        elif process.returncode is None:
            process.kill()
        await process.wait()


def _signal_process_group(process_group_id: int, signal_number: signal.Signals) -> None:
    if os.name != "posix":
        return
    try:
        os.killpg(process_group_id, signal_number)
    except ProcessLookupError:
        return


def _resolve_cwd(arguments: Mapping[str, object], context: ToolExecutionContext) -> Path:
    value = _optional_string(arguments, "cwd")
    if value is None:
        return context.working_directory
    resolved = resolve_context_tool_path(context, value)
    if not resolved.is_dir():
        raise ToolPathError(f"command working directory does not exist: {resolved}")
    return resolved


def _format_output(output: bytearray, *, truncated: bool) -> str:
    text = output.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()
    if not text:
        text = "(no output)"
    if len(text) > _MAX_OUTPUT_CHARS:
        text = f"{text[:_MAX_OUTPUT_CHARS]}\n...[truncated]..."
    elif truncated:
        text = f"{text}\n...[truncated]..."
    return text


def _timeout_result(
    *,
    command: str,
    timeout_seconds: int,
    cwd: Path,
    output: bytearray,
    output_truncated: bool,
    returncode: int | None,
) -> ToolExecutionResult:
    partial = _format_output(output, truncated=output_truncated)
    text = f"Command timed out after {timeout_seconds} seconds."
    if partial != "(no output)":
        text = f"{text}\n\nPartial output:\n{partial}"
    hint = _interactive_command_hint(command=command, output=partial)
    if hint:
        text = f"{text}\n\n{hint}"
    return _failure(
        "command_timed_out",
        f"command exceeded {timeout_seconds} seconds",
        attempted=True,
        text=text,
        data={
            "command": command,
            "cwd": str(cwd),
            "returncode": returncode,
            "timed_out": True,
            "output_truncated": output_truncated,
        },
    )


def _preflight_interactive_command(command: str) -> str | None:
    lowered_command = command.lower()
    if not _looks_like_interactive_scaffold(lowered_command):
        return None
    return (
        "This command appears to require interactive input before it can continue. "
        "The bash tool is non-interactive, so it cannot answer installer/scaffold prompts "
        "live. Prefer non-interactive flags (for example --yes, -y, --skip-install, "
        "--defaults, --non-interactive), or run the scaffolding step once in an external "
        "terminal before asking the agent to continue."
    )


def _interactive_command_hint(*, command: str, output: str) -> str | None:
    if _looks_like_interactive_scaffold(command.lower()) or _looks_like_prompt(output):
        return (
            "This command appears to require interactive input. The bash tool is "
            "non-interactive, so prefer non-interactive flags (for example --yes, -y, "
            "--skip-install, or similar) or run the scaffolding step once in an external "
            "terminal before continuing."
        )
    return None


def _looks_like_interactive_scaffold(lowered_command: str) -> bool:
    scaffold_markers = (
        "create-next-app",
        "npm create ",
        "pnpm create ",
        "yarn create ",
        "bun create ",
        "pnpm dlx ",
        "npm init ",
        "pnpm init ",
        "yarn init ",
        "bunx create-",
        "npx create-",
    )
    non_interactive_markers = (
        "--yes",
        " -y",
        "--skip-install",
        "--defaults",
        "--non-interactive",
        "--ci",
    )
    return any(marker in lowered_command for marker in scaffold_markers) and not any(
        marker in lowered_command for marker in non_interactive_markers
    )


def _looks_like_prompt(output: str) -> bool:
    prompt_markers = (
        "would you like",
        "ok to proceed",
        "select an option",
        "which",
        "press enter to continue",
        "?",
    )
    lowered_output = output.lower()
    return any(marker in lowered_output for marker in prompt_markers)


def _failure(
    code: str,
    message: str,
    *,
    attempted: bool,
    text: str = "",
    data: Mapping[str, object] | None = None,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        status=ToolExecutionStatus.FAILURE,
        text=text,
        data=data or {},
        error=ToolExecutionError(code, message),
        backend_attempted=attempted,
    )


def _string(arguments: Mapping[str, object], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _optional_string(arguments: Mapping[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value


def _integer(arguments: Mapping[str, object], name: str, *, default: int) -> int:
    value = arguments.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def build_bash_tool() -> RegisteredTool:
    """Create the HomeMaster Bash registration."""

    definition = ToolDefinition(
        internal_id="homemaster.bash.v1",
        model_alias="bash",
        description="Run a shell command in the local repository.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "cwd": {
                    "type": ["string", "null"],
                    "description": "Working directory override",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 600,
                    "default": 600,
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        verification_policy=VerificationPolicy(execution_proof=ExecutionProof.NONE),
        provenance=ToolProvenance(
            source="homemaster",
            reference=_IMPLEMENTATION_REFERENCE,
        ),
        version="2.0.0",
        concurrency_policy=ConcurrencyPolicy.SERIALIZED,
        state_effects=("process.exec",),
        required_capabilities=("process.exec",),
    )
    return RegisteredTool(definition, BashExecutor())


__all__ = ["build_bash_tool"]
