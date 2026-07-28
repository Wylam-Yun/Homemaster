"""HomeMaster execution adapters for service-backed default tools."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter

from homemaster.services.lsp import (
    find_references,
    go_to_definition,
    hover,
    list_document_symbols,
    workspace_symbol_search,
)
from homemaster.tools.base import ToolExecutionContext as BaseToolExecutionContext
from homemaster.tools.base import ToolResult
from homemaster.tools.contracts import (
    ConcurrencyPolicy,
    ExecutionProof,
    RegisteredTool,
    ResultImage,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
    VerificationRecord,
    VerificationStatus,
)
from homemaster.tools.image_generation import ImageGenerationTool
from homemaster.tools.image_to_text import ImageToTextTool
from homemaster.tools.runtime_services import HomeToolServices

_IMPLEMENTATION_REFERENCE = "homemaster.tools.service_tools"

_SERVICE_TOOL_NAMES = (
    "ask_user_question",
    "lsp",
    "mcp_auth",
    "image_to_text",
    "image_generation",
    "config",
    "enter_plan_mode",
    "exit_plan_mode",
    "cron_create",
    "cron_list",
    "cron_delete",
    "cron_toggle",
    "remote_trigger",
    "task_create",
    "task_get",
    "task_list",
    "task_stop",
    "task_output",
    "task_update",
    "agent",
    "send_message",
    "team_create",
    "team_delete",
)

_READ_ONLY = {
    "ask_user_question",
    "lsp",
    "image_to_text",
    "cron_list",
    "task_get",
    "task_list",
    "task_output",
}

_PROCESS_TOOLS = {
    "remote_trigger",
    "task_create",
    "task_stop",
    "agent",
    "send_message",
}

_SCHEDULER_TOOLS = {
    "cron_create",
    "cron_list",
    "cron_delete",
    "cron_toggle",
    "remote_trigger",
}

_SPAWN_TOOLS = {
    "task_create",
    "task_get",
    "task_list",
    "task_stop",
    "task_output",
    "task_update",
    "agent",
    "send_message",
    "team_create",
    "team_delete",
}


def validate_cron_expression(expression: str) -> bool:
    return croniter.is_valid(expression)


def validate_timezone(timezone: str | None) -> bool:
    if not timezone:
        return True
    try:
        ZoneInfo(timezone)
    except Exception:
        return False
    return True


@dataclass(frozen=True)
class ServiceToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    defaults: dict[str, Any]


class HomeServiceExecutor:
    """Run a service-backed tool behind HomeMaster's execution contract."""

    def __init__(self, spec: ServiceToolSpec) -> None:
        self._spec = spec

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        try:
            parsed = SimpleNamespace(**_arguments_with_defaults(self._spec, arguments))
            metadata: dict[str, Any] = dict(context.services)
            if self._spec.name == "ask_user_question" and not callable(
                metadata.get("ask_user_prompt")
            ):
                return ToolExecutionResult(
                    status=ToolExecutionStatus.SUCCESS,
                    text=parsed.question,
                    data={
                        "waiting_user": True,
                        "question": parsed.question,
                        "tool_call_id": context.tool_call_id,
                    },
                    backend_attempted=True,
                )
            services = metadata.get("tool_services")
            if isinstance(services, HomeToolServices) and self._spec.name in _HOME_OWNED:
                return await _execute_home_owned(self._spec.name, parsed, context, services)
            result = await _execute_ported_tool(self._spec.name, parsed, context, metadata)
        except Exception as exc:
            return ToolExecutionResult(
                status=ToolExecutionStatus.FAILURE,
                error=ToolExecutionError(
                    "homemaster_service_error",
                    f"{self._spec.name} failed: {type(exc).__name__}: {exc}",
                ),
                backend_attempted=True,
            )
        if result.is_error:
            return ToolExecutionResult(
                status=ToolExecutionStatus.FAILURE,
                text=result.output,
                data=result.metadata,
                error=ToolExecutionError("homemaster_tool_error", result.output),
                backend_attempted=True,
            )
        images: tuple[ResultImage, ...] = ()
        data = dict(result.metadata)
        if self._spec.name == "image_generation":
            try:
                images, receipts = _generated_image_receipts(data)
            except (OSError, ValueError) as exc:
                return ToolExecutionResult(
                    status=ToolExecutionStatus.OUTCOME_UNKNOWN,
                    text=result.output,
                    data=data,
                    error=ToolExecutionError(
                        "image_terminal_state_unreadable",
                        f"image_generation wrote files but verification setup failed: {exc}",
                    ),
                    backend_attempted=True,
                )
            data["files"] = receipts
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text=result.output,
            data=data,
            images=images,
            backend_attempted=True,
        )


class _ImageGenerationVerifier:
    async def verify(
        self,
        result: ToolExecutionResult,
        context: ToolExecutionContext,
    ) -> VerificationRecord:
        del context
        files = result.data.get("files")
        if not isinstance(files, tuple) or not files:
            return VerificationRecord(
                status=VerificationStatus.FAILED,
                detail="image generation receipt contains no files",
                evidence_refs=("image-generation/missing-receipt",),
            )
        evidence: list[str] = []
        for item in files:
            if not isinstance(item, Mapping):
                return VerificationRecord(
                    status=VerificationStatus.FAILED,
                    detail="image generation receipt is malformed",
                    evidence_refs=("image-generation/malformed-receipt",),
                )
            path = Path(str(item.get("path", "")))
            expected = str(item.get("sha256", ""))
            try:
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as exc:
                return VerificationRecord(
                    status=VerificationStatus.FAILED,
                    detail=f"generated image cannot be reopened: {exc}",
                    evidence_refs=(f"image-generation/{expected or 'unreadable'}",),
                )
            evidence.append(f"image-generation/{actual}")
            if actual != expected:
                return VerificationRecord(
                    status=VerificationStatus.FAILED,
                    detail="generated image hash does not match execution receipt",
                    evidence_refs=tuple(evidence),
                )
        return VerificationRecord(
            status=VerificationStatus.PASSED,
            detail="generated image files independently reopened and hashed",
            evidence_refs=tuple(evidence),
        )


def _generated_image_receipts(
    metadata: Mapping[str, object],
) -> tuple[tuple[ResultImage, ...], list[dict[str, object]]]:
    raw_paths = metadata.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ValueError("provider returned no generated image paths")
    images: list[ResultImage] = []
    receipts: list[dict[str, object]] = []
    for raw_path in raw_paths:
        path = Path(str(raw_path)).expanduser().resolve(strict=True)
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        media_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(path.suffix.lower(), "image/png")
        images.append(
            ResultImage(
                media_type=media_type,
                data_base64=base64.b64encode(content).decode("ascii"),
                content_sha256=digest,
            )
        )
        receipts.append(
            {
                "path": str(path),
                "sha256": digest,
                "byte_count": len(content),
                "media_type": media_type,
            }
        )
    return tuple(images), receipts


_HOME_OWNED = {
    "mcp_auth",
    "config",
    "enter_plan_mode",
    "exit_plan_mode",
    "cron_create",
    "cron_list",
    "cron_delete",
    "cron_toggle",
    "remote_trigger",
    "task_create",
    "task_get",
    "task_list",
    "task_stop",
    "task_output",
    "task_update",
    "agent",
    "send_message",
    "team_create",
    "team_delete",
}


async def _execute_ported_tool(
    name: str,
    arguments: Any,
    context: ToolExecutionContext,
    metadata: dict[str, Any],
) -> Any:
    if name == "ask_user_question":
        prompt = metadata.get("ask_user_prompt")
        if not callable(prompt):
            return ToolResult(arguments.question)
        answer = prompt(arguments.question)
        return ToolResult(str(await answer if inspect.isawaitable(answer) else answer))
    if name == "lsp":
        return _execute_lsp(arguments, context.working_directory)
    tool_context = BaseToolExecutionContext(
        cwd=context.working_directory,
        metadata=metadata,
    )
    if name == "image_to_text":
        return await ImageToTextTool().execute(arguments, tool_context)
    if name == "image_generation":
        return await ImageGenerationTool().execute(arguments, tool_context)
    raise ValueError(f"unsupported service tool: {name}")


def _execute_lsp(arguments: Any, root: Path) -> ToolResult:
    root = root.resolve()
    if arguments.operation == "workspace_symbol":
        if not arguments.query:
            return ToolResult("workspace_symbol requires query", is_error=True)
        return ToolResult(_format_symbols(workspace_symbol_search(root, arguments.query), root))
    if not arguments.file_path:
        return ToolResult(f"{arguments.operation} requires file_path", is_error=True)
    path = Path(arguments.file_path).expanduser()
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.exists():
        return ToolResult(f"File not found: {path}", is_error=True)
    if path.suffix != ".py":
        return ToolResult("The lsp tool currently supports Python files only.", is_error=True)
    if arguments.operation == "document_symbol":
        return ToolResult(_format_symbols(list_document_symbols(path), root))
    kwargs = {
        "root": root,
        "file_path": path,
        "symbol": arguments.symbol,
        "line": arguments.line,
        "character": arguments.character,
    }
    if not arguments.symbol and arguments.line is None:
        return ToolResult(
            f"{arguments.operation} requires symbol or line",
            is_error=True,
        )
    if arguments.operation == "go_to_definition":
        return ToolResult(_format_symbols(go_to_definition(**kwargs), root))
    if arguments.operation == "find_references":
        refs = find_references(**kwargs)
        output = "\n".join(
            f"{_display_path(item_path, root)}:{line}:{text}" for item_path, line, text in refs
        )
        return ToolResult(output or "(no results)")
    result = hover(**kwargs)
    if result is None:
        return ToolResult("(no hover result)")
    output = [
        f"{result.kind} {result.name}",
        f"path: {_display_path(result.path, root)}:{result.line}:{result.character}",
    ]
    if result.signature:
        output.append(f"signature: {result.signature}")
    if result.docstring:
        output.append(f"docstring: {result.docstring.strip()}")
    return ToolResult("\n".join(output))


def _format_symbols(results: list[Any], root: Path) -> str:
    if not results:
        return "(no results)"
    lines: list[str] = []
    for item in results:
        lines.append(
            f"{item.kind} {item.name} - "
            f"{_display_path(item.path, root)}:{item.line}:{item.character}"
        )
        if item.signature:
            lines.append(f"  signature: {item.signature}")
        if item.docstring:
            lines.append(f"  docstring: {item.docstring.strip()}")
    return "\n".join(lines)


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


async def _execute_home_owned(
    name: str,
    arguments: Any,
    context: ToolExecutionContext,
    services: HomeToolServices,
) -> ToolExecutionResult:
    try:
        if name == "mcp_auth":
            return await _mcp_auth(arguments, context, services)
        if name == "config":
            return _config(arguments, services)
        if name in {"enter_plan_mode", "exit_plan_mode"}:
            enabled = name == "enter_plan_mode"
            services.plan_mode.set(context.session_id, enabled)
            mode = "plan" if enabled else "default"
            return _success(f"Permission mode set to {mode}", mode=mode)
        if name.startswith("cron_"):
            return _cron(name, arguments, context, services)
        if name == "remote_trigger":
            return await _remote_trigger(arguments, context, services)
        if name.startswith("task_"):
            return await _task(name, arguments, context, services)
        if name == "agent":
            return await _agent(arguments, context, services)
        if name == "send_message":
            return await _send_message(arguments, services)
        if name in {"team_create", "team_delete"}:
            return _team(name, arguments, services)
    except ValueError as exc:
        return _failure("homemaster_tool_error", str(exc), backend_attempted=False)
    except Exception as exc:
        return ToolExecutionResult(
            status=ToolExecutionStatus.OUTCOME_UNKNOWN,
            error=ToolExecutionError(
                "homemaster_service_error",
                f"{name} failed: {type(exc).__name__}: {exc}",
            ),
            backend_attempted=True,
        )
    return _failure("homemaster_service_error", f"Unsupported Home service tool: {name}")


async def _mcp_auth(
    arguments: Any,
    context: ToolExecutionContext,
    services: HomeToolServices,
) -> ToolExecutionResult:
    manager = context.services.get("mcp_manager")
    if manager is None:
        return _failure(
            "mcp_unavailable",
            "mcp_auth is unavailable because no MCP manager is configured",
            backend_attempted=False,
        )
    updated = services.config.set_mcp_auth(
        arguments.server_name,
        arguments.mode,
        arguments.value,
        arguments.key,
    )
    manager.update_server_config(arguments.server_name, updated)
    await manager.reconnect_all()
    statuses = {item.name: item for item in manager.list_statuses()}
    status = statuses[arguments.server_name]
    if status.state != "connected":
        return _failure(
            "mcp_reconnect_failed",
            f"Saved MCP auth for {arguments.server_name}, but reconnect failed: {status.detail}",
            backend_attempted=True,
            server=arguments.server_name,
            state=status.state,
        )
    return _success(
        f"Saved MCP auth for {arguments.server_name}",
        server=arguments.server_name,
        state=status.state,
        auth_configured=status.auth_configured,
    )


def _config(arguments: Any, services: HomeToolServices) -> ToolExecutionResult:
    if arguments.action == "show":
        return _success(services.config.show(), action="show")
    if arguments.action == "set" and arguments.key and arguments.value is not None:
        path = services.config.set(arguments.key, arguments.value)
        return _success(
            f"Updated {arguments.key}",
            action="set",
            key=arguments.key,
            config_path=str(path),
        )
    return _failure(
        "homemaster_tool_error",
        "Usage: action=show or action=set with key/value",
        backend_attempted=False,
    )


def _cron(
    name: str,
    arguments: Any,
    context: ToolExecutionContext,
    services: HomeToolServices,
) -> ToolExecutionResult:
    store = services.cron
    if name == "cron_list":
        jobs = store.load()
        if not jobs:
            return _success(
                "No cron jobs configured.",
                jobs=[],
                scheduler_pid=store.scheduler_pid(),
            )
        lines = [f"Scheduler: {'running' if store.scheduler_pid() else 'stopped'}", ""]
        for job in jobs:
            lines.append(
                f"[{'on' if job.get('enabled', True) else 'off'}] {job['name']}  "
                f"{job.get('schedule', '?')}\n     cmd: {job.get('command') or '(agent_turn)'}"
            )
        return _success("\n".join(lines), jobs=jobs, scheduler_pid=store.scheduler_pid())
    if name == "cron_delete":
        store.delete(arguments.name)
        return _success(f"Deleted cron job '{arguments.name}'", name=arguments.name, deleted=True)
    if name == "cron_toggle":
        job = store.toggle(arguments.name, arguments.enabled)
        state = "enabled" if arguments.enabled else "disabled"
        return _success(f"Cron job '{arguments.name}' {state}", job=job)
    if not validate_cron_expression(arguments.schedule):
        return _failure(
            "homemaster_tool_error",
            f"Invalid cron expression: {arguments.schedule!r}",
            backend_attempted=False,
        )
    if not validate_timezone(arguments.timezone):
        return _failure(
            "homemaster_tool_error",
            f"Invalid timezone: {arguments.timezone!r}",
            backend_attempted=False,
        )
    payload = dict(arguments.payload or {})
    if arguments.message:
        payload.setdefault("kind", "agent_turn")
        payload.setdefault("message", arguments.message)
    if not arguments.command and not payload.get("message"):
        return _failure(
            "homemaster_tool_error",
            "Cron job requires command or message.",
            backend_attempted=False,
        )
    job: dict[str, Any] = {
        "name": arguments.name,
        "schedule": arguments.schedule,
        "cwd": arguments.cwd or str(context.working_directory),
        "enabled": arguments.enabled,
    }
    if arguments.command is not None:
        job["command"] = arguments.command
    if arguments.timezone:
        job["timezone"] = arguments.timezone
    if payload:
        job["payload"] = payload
    if arguments.notify is not None:
        job["notify"] = arguments.notify
    stored = store.upsert(job)
    status = "enabled" if arguments.enabled else "disabled"
    return _success(
        f"Created cron job '{arguments.name}' [{arguments.schedule}] ({status})",
        job=stored,
        registry_path=str(store.registry_path),
    )


async def _remote_trigger(
    arguments: Any,
    context: ToolExecutionContext,
    services: HomeToolServices,
) -> ToolExecutionResult:
    job = services.cron.get(arguments.name)
    if job is None:
        return _failure(
            "homemaster_tool_error",
            f"No cron job named '{arguments.name}'",
            backend_attempted=False,
        )
    command = job.get("command")
    if not isinstance(command, str) or not command:
        return _failure(
            "homemaster_tool_error",
            "Remote trigger currently requires a command cron job",
            backend_attempted=False,
        )
    task = await services.tasks.create_shell_task(
        command=command,
        description=f"cron:{arguments.name}",
        cwd=job.get("cwd") or context.working_directory,
    )
    deadline = asyncio.get_running_loop().time() + arguments.timeout_seconds
    while task.status == "running" and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.02)
    if task.status == "running":
        await services.tasks.stop_task(task.id)
        return _failure(
            "cron_trigger_timeout",
            f"Cron job '{arguments.name}' timed out",
            backend_attempted=True,
            task_id=task.id,
        )
    data = _task_data(task)
    if task.return_code != 0:
        return _failure(
            "cron_command_failed",
            f"Cron job '{arguments.name}' failed with return code {task.return_code}",
            backend_attempted=True,
            **data,
        )
    return _success(f"Triggered cron job '{arguments.name}'", **data)


async def _task(
    name: str,
    arguments: Any,
    context: ToolExecutionContext,
    services: HomeToolServices,
) -> ToolExecutionResult:
    manager = services.tasks
    if name == "task_create":
        if arguments.type == "local_bash":
            if not arguments.command:
                return _failure(
                    "homemaster_tool_error",
                    "command is required for local_bash tasks",
                    backend_attempted=False,
                )
            task = await manager.create_shell_task(
                command=arguments.command,
                description=arguments.description,
                cwd=context.working_directory,
            )
        elif arguments.type == "local_agent":
            if not arguments.prompt:
                return _failure(
                    "homemaster_tool_error",
                    "prompt is required for local_agent tasks",
                    backend_attempted=False,
                )
            task = await manager.create_agent_task(
                prompt=arguments.prompt,
                description=arguments.description,
                cwd=context.working_directory,
                model=arguments.model,
            )
        else:
            return _failure(
                "homemaster_tool_error",
                f"unsupported task type: {arguments.type}",
                backend_attempted=False,
            )
        return _success(f"Created task {task.id} ({task.type})", **_task_data(task))
    task = manager.get_task(arguments.task_id) if name != "task_list" else None
    if name != "task_list" and task is None:
        return _failure(
            "homemaster_tool_error",
            f"No task found with ID: {arguments.task_id}",
            backend_attempted=False,
        )
    if name == "task_get":
        assert task is not None
        return _success(str(task), **_task_data(task))
    if name == "task_list":
        tasks = manager.list_tasks(status=arguments.status)
        text = (
            "(no tasks)"
            if not tasks
            else "\n".join(
                f"{item.id} {item.type} {item.status} {item.description}" for item in tasks
            )
        )
        return _success(text, tasks=[_task_data(item) for item in tasks])
    if name == "task_stop":
        stopped = await manager.stop_task(arguments.task_id)
        return _success(f"Stopped task {stopped.id}", **_task_data(stopped))
    if name == "task_output":
        output = manager.read_task_output(arguments.task_id, max_bytes=arguments.max_bytes)
        assert task is not None
        return _success(output or "(no output)", output=output, **_task_data(task))
    assert name == "task_update"
    updated = manager.update_task(
        arguments.task_id,
        description=arguments.description,
        progress=arguments.progress,
        status_note=arguments.status_note,
    )
    return _success(f"Updated task {updated.id}", **_task_data(updated))


async def _agent(
    arguments: Any,
    context: ToolExecutionContext,
    services: HomeToolServices,
) -> ToolExecutionResult:
    if arguments.mode not in {"local_agent", "remote_agent", "in_process_teammate"}:
        return _failure(
            "homemaster_tool_error",
            "Invalid mode. Use local_agent, remote_agent, or in_process_teammate.",
            backend_attempted=False,
        )
    task = await services.tasks.create_agent_task(
        prompt=arguments.prompt,
        description=arguments.description,
        cwd=context.working_directory,
        task_type=arguments.mode,
        model=arguments.model,
        command=arguments.command,
    )
    team = arguments.team or "default"
    agent_id = f"{arguments.subagent_type or 'agent'}@{team}"
    services.agent_tasks[agent_id] = task.id
    if arguments.team:
        services.teams.add_agent(arguments.team, task.id)
    return _success(
        f"Spawned agent {agent_id} (task_id={task.id}, backend=subprocess)",
        agent_id=agent_id,
        task_id=task.id,
        backend_type="subprocess",
        description=arguments.description,
    )


async def _send_message(
    arguments: Any,
    services: HomeToolServices,
) -> ToolExecutionResult:
    task_id = services.agent_tasks.get(arguments.task_id, arguments.task_id)
    await services.tasks.write_to_task(task_id, arguments.message)
    return _success(
        f"Sent message to task {arguments.task_id}",
        task_id=task_id,
        message_sent=True,
    )


def _team(
    name: str,
    arguments: Any,
    services: HomeToolServices,
) -> ToolExecutionResult:
    if name == "team_create":
        team = services.teams.create_team(arguments.name, arguments.description)
        return _success(f"Created team {team.name}", team=asdict(team))
    services.teams.delete_team(arguments.name)
    return _success(f"Deleted team {arguments.name}", name=arguments.name, deleted=True)


def _task_data(task: Any) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "type": task.type,
        "status": task.status,
        "description": task.description,
        "output_file": str(task.output_file),
        "return_code": task.return_code,
        "metadata": dict(task.metadata),
    }


def _success(text: str, **data: Any) -> ToolExecutionResult:
    return ToolExecutionResult(
        status=ToolExecutionStatus.SUCCESS,
        text=text,
        data=data,
        backend_attempted=True,
    )


def _failure(
    code: str,
    message: str,
    *,
    backend_attempted: bool = True,
    **data: Any,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        status=ToolExecutionStatus.FAILURE,
        text=message,
        data=data,
        error=ToolExecutionError(code, message),
        backend_attempted=backend_attempted,
    )


def build_service_tools() -> tuple[RegisteredTool, ...]:
    specs = {spec.name: spec for spec in _load_service_specs()}
    tools: list[RegisteredTool] = []
    for name in _SERVICE_TOOL_NAMES:
        tools.append(
            RegisteredTool(
                definition=_definition(specs[name]),
                executor=HomeServiceExecutor(specs[name]),
                verifier=_ImageGenerationVerifier() if name == "image_generation" else None,
            )
        )
    return tuple(tools)


def _load_service_specs() -> tuple[ServiceToolSpec, ...]:
    resource = files("homemaster.tools").joinpath("service_tool_specs.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    return tuple(ServiceToolSpec(**item) for item in payload)


def _arguments_with_defaults(
    spec: ServiceToolSpec,
    arguments: Mapping[str, object],
) -> dict[str, Any]:
    values = dict(spec.defaults)
    values.update(arguments)
    return values


def _definition(tool: ServiceToolSpec) -> ToolDefinition:
    mutating = tool.name not in _READ_ONLY
    capabilities = ["tool.read" if not mutating else "tool.mutate"]
    state_effects: tuple[str, ...] = ()
    if tool.name in _PROCESS_TOOLS:
        capabilities.append("process.exec")
        state_effects = ("process.exec",)
    elif tool.name in {"image_to_text", "image_generation"}:
        capabilities.append("network.http")
        state_effects = ("network.http",) if mutating else ()
    elif mutating:
        state_effects = ("application.state",)
    if tool.name in _SCHEDULER_TOOLS:
        capabilities.append("scheduler.manage")
    if tool.name == "config":
        capabilities.append("config.mutate")
    if tool.name == "mcp_auth":
        capabilities.append("mcp.manage")
    if tool.name in _SPAWN_TOOLS:
        capabilities.append("process.spawn")
    return ToolDefinition(
        internal_id=f"homemaster.{tool.name}.v1",
        model_alias=tool.name,
        description=tool.description,
        input_schema=_schema_with_defaults(tool),
        output_schema={"type": "object"},
        verification_policy=VerificationPolicy(
            execution_proof=(
                ExecutionProof.EXTERNAL_STATE
                if tool.name == "image_generation"
                else ExecutionProof.STRUCTURED_RECEIPT
                if mutating
                else ExecutionProof.NONE
            )
        ),
        provenance=ToolProvenance(
            source="homemaster",
            reference=f"{_IMPLEMENTATION_REFERENCE}:{tool.name}",
        ),
        version="2.0.0",
        concurrency_policy=ConcurrencyPolicy.SERIALIZED if mutating else ConcurrencyPolicy.PARALLEL,
        state_effects=state_effects,
        required_capabilities=tuple(capabilities),
    )


def _schema_with_defaults(tool: ServiceToolSpec) -> dict[str, Any]:
    schema = json.loads(json.dumps(tool.input_schema))
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, default in tool.defaults.items():
            definition = properties.get(name)
            if isinstance(definition, dict):
                definition.setdefault("default", default)
    return schema


__all__ = ["build_service_tools"]
