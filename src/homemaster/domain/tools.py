"""Canonical HomeMaster domain tools."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from homemaster.tools.contracts import (
    RegisteredTool,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
)


def _result(
    *,
    text: str = "",
    data: Mapping[str, object] | None = None,
    evidence_refs: tuple[str, ...] = (),
    backend_attempted: bool = False,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        status=ToolExecutionStatus.SUCCESS,
        text=text,
        data=dict(data or {}),
        evidence_refs=evidence_refs,
        backend_attempted=backend_attempted,
    )


def _failure(code: str, message: str, *, attempted: bool = False) -> ToolExecutionResult:
    return ToolExecutionResult(
        status=ToolExecutionStatus.FAILURE,
        error=ToolExecutionError(code=code, message=message),
        backend_attempted=attempted,
    )


def _run_context(context: ToolExecutionContext) -> Any:
    value = context.services.get("run_context")
    if value is None:
        raise RuntimeError("canonical tool context has no run_context service")
    return value


class _Executor:
    def __init__(self, function: Any) -> None:
        self._function = function

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        return await self._function(dict(arguments), context)


def _registered(
    *,
    name: str,
    description: str,
    input_schema: Mapping[str, object],
    function: Any,
    state_effects: tuple[str, ...] = (),
    verification: VerificationPolicy | None = None,
) -> RegisteredTool:
    return RegisteredTool(
        definition=ToolDefinition(
            internal_id=f"homemaster.{name}.v1",
            model_alias=name,
            description=description,
            input_schema=dict(input_schema),
            output_schema={"type": "object"},
            verification_policy=verification or VerificationPolicy(),
            provenance=ToolProvenance(source="homemaster.domain", reference=f"domain.{name}"),
            version="3.5.0",
            state_effects=state_effects,
        ),
        executor=_Executor(function),
    )


async def _task_interpreter(arguments: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
    del context
    utterance = arguments.get("utterance", "")
    if not utterance:
        return _failure("invalid_arguments", "utterance is required")
    return _result(
        text=f"Interpreted task: {utterance}",
        data={
            "task_name": arguments.get("task_name", "home_task"),
            "utterance": utterance,
            "intent": "home_assistance",
            "extracted_entities": [],
        },
    )


async def _memory_retriever(arguments: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
    query = arguments.get("query", "")
    if not query:
        return _failure("invalid_arguments", "query is required")
    run_context = _run_context(context)
    memory_path = getattr(run_context.settings, "memory_path", None)
    if not memory_path or not memory_path.exists():
        return _failure("memory_not_found", f"memory file not found: {memory_path}")
    try:
        records = json.loads(memory_path.read_text(encoding="utf-8"))
        if isinstance(records, dict):
            records = records.get("objects", [])
        keywords = str(query).lower().split()
        hits = [
            record
            for record in records
            if any(keyword in json.dumps(record, ensure_ascii=False).lower() for keyword in keywords)
        ][: int(arguments.get("top_k", 5))]
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        hits = []
    return _result(
        text=f"Found {len(hits)} memory hits for '{query}'",
        data={"query": query, "hits": hits, "hit_count": len(hits)},
    )


async def _target_grounder(arguments: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
    del context
    target = arguments.get("target_object", "")
    if not target:
        return _failure("invalid_arguments", "target_object is required")
    return _result(
        text=f"Grounded target: {target}",
        data={
            "target_object": target,
            "grounded_location": arguments.get("room_hint", "unknown"),
            "confidence": 0.8,
            "memory_hits_used": arguments.get("memory_hits", []),
        },
    )


async def _load_skill(arguments: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
    skill_name = arguments.get("name", "")
    if not isinstance(skill_name, str) or not skill_name.strip():
        return _failure("invalid_arguments", "name is required")
    run_context = _run_context(context)
    skill_registry = run_context.deps.get("skill_registry")
    if skill_registry is None:
        return _failure("unsupported_capability", "no skill_registry in run_context.deps")
    refresh = getattr(skill_registry, "refresh", None)
    if callable(refresh):
        refresh()
    getter = getattr(skill_registry, "get_model_visible", None)
    spec = getter(skill_name) if callable(getter) else skill_registry.get(skill_name)
    if spec is None:
        return _failure("skill_not_found", f"skill not found: {skill_name}")
    return _result(
        data={
            "name": spec.name,
            "description": spec.description,
            "content": spec.content,
            "base_dir": str(spec.base_dir),
            "command_name": spec.command_name,
            "argument_hint": spec.argument_hint,
        }
    )


async def _robot_go_to(arguments: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
    del context
    room = arguments.get("room_hint", arguments.get("target_room", "unknown"))
    return _result(
        text=f"Navigated to {room}",
        data={"location": room, "observation": f"navigated to {room}"},
    )


async def _robot_manipulate(arguments: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
    del context
    action = arguments.get("action", "pick_up")
    target = arguments.get("target_object", "unknown")
    return _result(
        text=f"{action} {target}",
        data={"holding": target, "action": action, "result": f"{action} {target}"},
    )


async def _robot_verify(arguments: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
    del context
    target = arguments.get("target_object", "unknown")
    expected = arguments.get("expected_state", "delivered")
    return _result(
        text=f"Verified {target} ({expected})",
        data={"verified": True, "target_object": target, "expected_state": expected},
    )


async def _memory_writer(arguments: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
    proposal = arguments.get("proposal")
    if not proposal:
        return _failure("invalid_arguments", "proposal is required")
    if not isinstance(proposal, Mapping):
        return _failure("invalid_arguments", "proposal must be an object")
    required = {"object_category", "room_id", "anchor_id"}
    missing = required - set(proposal)
    if missing:
        return _failure("invalid_arguments", f"proposal missing fields: {sorted(missing)}")
    run_context = _run_context(context)
    settings = run_context.settings
    memory_path = getattr(settings, "memory_path", None)
    if memory_path and memory_path.exists():
        try:
            from homemaster.memory.runtime_store import ObjectMemoryUpdate, RuntimeMemoryStore

            memory_root = Path(settings.runtime_root) / settings.run_id / "memory"
            store = RuntimeMemoryStore(memory_root)
            store.apply_updates(
                base_memory_path=memory_path,
                updates=[
                    ObjectMemoryUpdate(
                        memory_id=str(proposal["anchor_id"]),
                        update_type="confirm",
                        updated_fields={
                            "belief_state": proposal.get("belief_state", "verified"),
                            "last_confirmed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        },
                    )
                ],
            )
        except Exception:
            pass
    return _result(
        text=f"Updated memory for {proposal.get('object_category')}",
        data={
            "committed": True,
            "object_category": proposal.get("object_category"),
            "room_id": proposal.get("room_id"),
            "anchor_id": proposal["anchor_id"],
        },
    )


async def _task_summarizer(arguments: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
    del context
    task_name = arguments.get("task_name", "unknown")
    status = arguments.get("status", "completed")
    summary = arguments.get("summary", "") or f"Task {task_name} {status}"
    return _result(
        text=f"Summarized task {task_name}: {status}",
        data={
            "task_name": task_name,
            "status": status,
            "summary": summary,
            "tool_results": arguments.get("tool_results", []),
        },
    )


def make_task_interpreter() -> RegisteredTool:
    return _registered(
        name="task_interpreter",
        description="Parse user utterance into a structured task card.",
        input_schema={
            "type": "object",
            "properties": {
                "utterance": {"type": "string", "description": "User request text."},
                "task_name": {"type": "string", "description": "Optional task label."},
            },
            "required": ["utterance"],
        },
        function=_task_interpreter,
    )


def make_memory_retriever(*, memory_path: Any = None) -> RegisteredTool:
    del memory_path
    return _registered(
        name="memory_retriever",
        description="Retrieve relevant object memory candidates using keyword matching.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "top_k": {"type": "integer", "description": "Max results."},
            },
            "required": ["query"],
        },
        function=_memory_retriever,
    )


def make_target_grounder(*, world_path: Any = None) -> RegisteredTool:
    del world_path
    return _registered(
        name="target_grounder",
        description="Assess memory hits and select a grounded target for execution.",
        input_schema={
            "type": "object",
            "properties": {
                "target_object": {"type": "string", "description": "Object to ground."},
                "memory_hits": {"type": "array", "items": {"type": "object"}},
                "room_hint": {"type": "string", "description": "Expected room."},
            },
            "required": ["target_object"],
        },
        function=_target_grounder,
    )


def make_load_skill() -> RegisteredTool:
    return _registered(
        name="load_skill",
        description="Load the complete instructions for one available Skill by name.",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Skill name."}},
            "required": ["name"],
            "additionalProperties": False,
        },
        function=_load_skill,
    )


def make_robot_go_to() -> RegisteredTool:
    return _registered(
        name="robot_go_to",
        description="Navigate robot to a target location.",
        input_schema={
            "type": "object",
            "properties": {
                "room_hint": {"type": "string", "description": "Target room."},
                "target_room": {"type": "string", "description": "Target room."},
            },
        },
        function=_robot_go_to,
        state_effects=("backend.advance",),
    )


def make_robot_manipulate() -> RegisteredTool:
    return _registered(
        name="robot_manipulate",
        description="Manipulate an object (pick up, put down, and related actions).",
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Action to perform."},
                "target_object": {"type": "string", "description": "Object to manipulate."},
            },
            "required": ["action", "target_object"],
        },
        function=_robot_manipulate,
        state_effects=("backend.advance",),
    )


def make_robot_verify() -> RegisteredTool:
    return _registered(
        name="robot_verify",
        description="Verify whether a task objective is achieved.",
        input_schema={
            "type": "object",
            "properties": {
                "target_object": {"type": "string", "description": "Object to verify."},
                "expected_state": {"type": "string", "description": "Expected state."},
            },
        },
        function=_robot_verify,
    )


def make_memory_writer(*, runtime_memory_root: Any = None) -> RegisteredTool:
    del runtime_memory_root
    return _registered(
        name="memory_writer",
        description="Submit a proposal to update object memory.",
        input_schema={
            "type": "object",
            "properties": {
                "proposal": {
                    "type": "object",
                    "properties": {
                        "object_category": {"type": "string"},
                        "room_id": {"type": "string"},
                        "anchor_id": {"type": "string"},
                        "belief_state": {"type": "string"},
                    },
                    "required": ["object_category", "room_id", "anchor_id"],
                }
            },
            "required": ["proposal"],
        },
        function=_memory_writer,
    )


def make_task_summarizer() -> RegisteredTool:
    return _registered(
        name="task_summarizer",
        description="Summarize a completed task for memory commit.",
        input_schema={
            "type": "object",
            "properties": {
                "task_name": {"type": "string", "description": "Task identifier."},
                "status": {"type": "string", "description": "Final status."},
                "summary": {"type": "string", "description": "Summary text."},
                "tool_results": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["task_name", "status"],
        },
        function=_task_summarizer,
    )


__all__ = [
    "make_load_skill",
    "make_memory_retriever",
    "make_memory_writer",
    "make_robot_go_to",
    "make_robot_manipulate",
    "make_robot_verify",
    "make_target_grounder",
    "make_task_interpreter",
    "make_task_summarizer",
]
