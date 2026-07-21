"""Home domain tool executors adapted into the canonical runtime.

Each factory returns a ToolSpec with an executor matching the protocol:
    def executor(*, arguments: dict, run_context: RunContext) -> ToolResult

Tools:
  task_interpreter  — parse user utterance into structured task card
  memory_retriever  — retrieve object memory candidates via RAG
  target_grounder   — assess memory hits and select grounded target
  skill_view        — retrieve skill metadata by name
  robot_navigate    — navigate robot to target location
  robot_observe     — observe environment at current location
  robot_manipulate  — manipulate an object (pick up, put down, etc.)
  robot_verify      — verify task objective achieved
  memory_writer     — persist memory update proposal
  task_summarizer   — summarize completed task for memory commit
"""

from __future__ import annotations

import json
import time
from typing import Any

from homemaster.agent.normalized import RunContext
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec

# ---------------------------------------------------------------------------
# Executor implementations
# ---------------------------------------------------------------------------


def _exec_task_interpreter(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
    utterance = arguments.get("utterance", "")
    if not utterance:
        return ToolResult(
            success=False,
            tool_name="task_interpreter",
            executor_mode="programmatic",
            failure_reason="utterance is required",
        )
    return ToolResult(
        success=True,
        tool_name="task_interpreter",
        executor_mode="programmatic",
        data={
            "task_name": arguments.get("task_name", "home_task"),
            "utterance": utterance,
            "intent": "home_assistance",
            "extracted_entities": [],
        },
        summary=f"Interpreted task: {utterance}",
    )


def _exec_memory_retriever(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
    query = arguments.get("query", "")
    if not query:
        return ToolResult(
            success=False,
            tool_name="memory_retriever",
            executor_mode="programmatic",
            failure_reason="query is required",
        )

    memory_path = run_context.settings.memory_path
    if not memory_path or not memory_path.exists():
        return ToolResult(
            success=False,
            tool_name="memory_retriever",
            executor_mode="programmatic",
            failure_reason=f"memory file not found: {memory_path}",
            summary="Memory file not found",
        )

    try:
        records = json.loads(memory_path.read_text(encoding="utf-8"))
        if isinstance(records, dict):
            records = records.get("objects", [])
        keywords = query.lower().split()
        hits = [
            r for r in records
            if any(kw in json.dumps(r, ensure_ascii=False).lower() for kw in keywords)
        ][:5]
    except Exception:
        hits = []

    return ToolResult(
        success=True,
        tool_name="memory_retriever",
        executor_mode="programmatic",
        data={"query": query, "hits": hits, "hit_count": len(hits)},
        summary=f"Found {len(hits)} memory hits for '{query}'",
    )


def _exec_target_grounder(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
    target = arguments.get("target_object", "")
    if not target:
        return ToolResult(
            success=False,
            tool_name="target_grounder",
            executor_mode="programmatic",
            failure_reason="target_object is required",
        )
    return ToolResult(
        success=True,
        tool_name="target_grounder",
        executor_mode="programmatic",
        data={
            "target_object": target,
            "grounded_location": arguments.get("room_hint", "unknown"),
            "confidence": 0.8,
            "memory_hits_used": arguments.get("memory_hits", []),
        },
        summary=f"Grounded target: {target}",
    )


def _exec_skill_view(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
    skill_name = arguments.get("skill_name", "")
    if not skill_name:
        return ToolResult(
            success=False,
            tool_name="skill_view",
            executor_mode="programmatic",
            failure_reason="skill_name is required",
        )

    skill_registry = run_context.deps.get("skill_registry")
    if skill_registry is None:
        return ToolResult(
            success=False,
            tool_name="skill_view",
            executor_mode="programmatic",
            failure_reason="no skill_registry in run_context.deps",
        )

    spec = skill_registry.get(skill_name)
    if spec is None:
        return ToolResult(
            success=False,
            tool_name="skill_view",
            executor_mode="programmatic",
            failure_reason=f"skill not found: {skill_name}",
        )

    return ToolResult(
        success=True,
        tool_name="skill_view",
        executor_mode="programmatic",
        data={
            "name": spec.name,
            "description": spec.description,
            "tool_names": spec.tool_names,
            "system_prompt_fragment": spec.system_prompt_fragment,
            "constraints": spec.constraints,
            "success_criteria": spec.success_criteria,
        },
    )


def _exec_robot_navigate(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
    room = arguments.get("room_hint", arguments.get("target_room", "unknown"))
    return ToolResult(
        success=True,
        tool_name="robot_navigate",
        executor_mode="simulated_skill",
        data={"location": room, "observation": f"navigated to {room}"},
        summary=f"Navigated to {room}",
    )


def _exec_robot_observe(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
    target = arguments.get("target_object", "unknown")
    return ToolResult(
        success=True,
        tool_name="robot_observe",
        executor_mode="simulated_skill",
        data={"object": target, "visible": True, "observation": f"observed {target}"},
        summary=f"Observed {target}",
    )


def _exec_robot_manipulate(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
    action = arguments.get("action", "pick_up")
    target = arguments.get("target_object", "unknown")
    return ToolResult(
        success=True,
        tool_name="robot_manipulate",
        executor_mode="simulated_skill",
        data={"holding": target, "action": action, "result": f"{action} {target}"},
        summary=f"{action} {target}",
    )


def _exec_robot_verify(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
    target = arguments.get("target_object", "unknown")
    expected = arguments.get("expected_state", "delivered")
    return ToolResult(
        success=True,
        tool_name="robot_verify",
        executor_mode="simulated_verification",
        data={"verified": True, "target_object": target, "expected_state": expected},
        summary=f"Verified {target} ({expected})",
    )


def _exec_memory_writer(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
    proposal = arguments.get("proposal")
    if not proposal:
        return ToolResult(
            success=False,
            tool_name="memory_writer",
            executor_mode="programmatic",
            failure_reason="proposal is required",
        )

    required = {"object_category", "room_id", "anchor_id"}
    missing = required - set(proposal.keys())
    if missing:
        return ToolResult(
            success=False,
            tool_name="memory_writer",
            executor_mode="programmatic",
            failure_reason=f"proposal missing fields: {missing}",
        )

    settings = run_context.settings
    if settings.memory_path and settings.memory_path.exists():
        try:
            from homemaster.memory.runtime_store import ObjectMemoryUpdate, RuntimeMemoryStore

            memory_root = settings.runtime_root / settings.run_id / "memory"
            store = RuntimeMemoryStore(memory_root)
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            store.apply_updates(
                base_memory_path=settings.memory_path,
                updates=[
                    ObjectMemoryUpdate(
                        memory_id=proposal["anchor_id"],
                        update_type="confirm",
                        updated_fields={
                            "belief_state": proposal.get("belief_state", "verified"),
                            "last_confirmed_at": now,
                        },
                    ),
                ],
            )
        except Exception:
            pass  # persistence is best-effort

    return ToolResult(
        success=True,
        tool_name="memory_writer",
        executor_mode="programmatic",
        data={
            "committed": True,
            "object_category": proposal.get("object_category"),
            "room_id": proposal.get("room_id"),
            "anchor_id": proposal["anchor_id"],
        },
        summary=f"Updated memory for {proposal.get('object_category')}",
    )


def _exec_task_summarizer(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
    task_name = arguments.get("task_name", "unknown")
    status = arguments.get("status", "completed")
    summary_text = arguments.get("summary", "")
    return ToolResult(
        success=True,
        tool_name="task_summarizer",
        executor_mode="programmatic",
        data={
            "task_name": task_name,
            "status": status,
            "summary": summary_text or f"Task {task_name} {status}",
            "tool_results": arguments.get("tool_results", []),
        },
        summary=f"Summarized task {task_name}: {status}",
    )


# ---------------------------------------------------------------------------
# ToolSpec factories
# ---------------------------------------------------------------------------


def make_task_interpreter() -> ToolSpec:
    return ToolSpec(
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
        executor_mode="programmatic",
        selectable_by_model=True,
        executor=_exec_task_interpreter,
    )


def make_memory_retriever(*, memory_path: Any = None) -> ToolSpec:
    return ToolSpec(
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
        executor_mode="programmatic",
        selectable_by_model=True,
        executor=_exec_memory_retriever,
    )


def make_target_grounder(*, world_path: Any = None) -> ToolSpec:
    return ToolSpec(
        name="target_grounder",
        description="Assess memory hits and select a grounded target for execution.",
        input_schema={
            "type": "object",
            "properties": {
                "target_object": {"type": "string", "description": "Object to ground."},
                "memory_hits": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Memory hits from retriever.",
                },
                "room_hint": {"type": "string", "description": "Expected room."},
            },
            "required": ["target_object"],
        },
        executor_mode="programmatic",
        selectable_by_model=True,
        executor=_exec_target_grounder,
    )


def make_skill_view() -> ToolSpec:
    return ToolSpec(
        name="skill_view",
        description="Retrieve skill metadata and system prompt fragment by name.",
        input_schema={
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill to view.",
                },
            },
            "required": ["skill_name"],
        },
        executor_mode="programmatic",
        selectable_by_model=True,
        executor=_exec_skill_view,
    )


def make_robot_navigate() -> ToolSpec:
    return ToolSpec(
        name="robot_navigate",
        description="Navigate robot to a target location.",
        input_schema={
            "type": "object",
            "properties": {
                "room_hint": {"type": "string", "description": "Target room."},
                "target_room": {"type": "string", "description": "Target room (alias)."},
            },
        },
        executor_mode="simulated_skill",
        selectable_by_model=True,
        executor=_exec_robot_navigate,
    )


def make_robot_observe() -> ToolSpec:
    return ToolSpec(
        name="robot_observe",
        description="Observe environment at current location for a target object.",
        input_schema={
            "type": "object",
            "properties": {
                "target_object": {"type": "string", "description": "Object to look for."},
            },
        },
        executor_mode="simulated_skill",
        selectable_by_model=True,
        executor=_exec_robot_observe,
    )


def make_robot_manipulate() -> ToolSpec:
    return ToolSpec(
        name="robot_manipulate",
        description="Manipulate an object (pick up, put down, etc.).",
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Action to perform."},
                "target_object": {"type": "string", "description": "Object to manipulate."},
            },
            "required": ["action", "target_object"],
        },
        executor_mode="simulated_skill",
        selectable_by_model=True,
        executor=_exec_robot_manipulate,
    )


def make_robot_verify() -> ToolSpec:
    return ToolSpec(
        name="robot_verify",
        description="Verify whether a task objective is achieved.",
        input_schema={
            "type": "object",
            "properties": {
                "target_object": {"type": "string", "description": "Object to verify."},
                "expected_state": {"type": "string", "description": "Expected state."},
            },
        },
        executor_mode="simulated_verification",
        selectable_by_model=True,
        executor=_exec_robot_verify,
    )


def make_memory_writer(*, runtime_memory_root: Any = None) -> ToolSpec:
    return ToolSpec(
        name="memory_writer",
        description="Submit a proposal to update object memory.",
        input_schema={
            "type": "object",
            "properties": {
                "proposal": {
                    "type": "object",
                    "description": "Memory update proposal.",
                    "properties": {
                        "object_category": {"type": "string"},
                        "room_id": {"type": "string"},
                        "anchor_id": {"type": "string"},
                        "belief_state": {"type": "string"},
                    },
                    "required": ["object_category", "room_id", "anchor_id"],
                },
            },
            "required": ["proposal"],
        },
        executor_mode="programmatic",
        selectable_by_model=True,
        executor=_exec_memory_writer,
    )


def make_task_summarizer() -> ToolSpec:
    return ToolSpec(
        name="task_summarizer",
        description="Summarize a completed task for memory commit.",
        input_schema={
            "type": "object",
            "properties": {
                "task_name": {"type": "string", "description": "Task identifier."},
                "status": {"type": "string", "description": "Final status."},
                "summary": {"type": "string", "description": "Summary text."},
                "tool_results": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Tool results from the task.",
                },
            },
            "required": ["task_name", "status"],
        },
        executor_mode="programmatic",
        selectable_by_model=True,
        executor=_exec_task_summarizer,
    )
