"""Compose the one ordinary-name tool Registry used by every environment."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from homemaster.domain.tools import (
    make_load_skill,
    make_memory_retriever,
    make_memory_writer,
    make_target_grounder,
    make_task_interpreter,
    make_task_summarizer,
)
from homemaster.task_state.tools import make_task_planner_tool, make_task_progress_check_tool
from homemaster.tools.adapters import from_registered_tool
from homemaster.tools.base import ToolRegistry
from homemaster.tools.bash import build_terminal_tool
from homemaster.tools.contracts import (
    ConcurrencyPolicy,
    ExecutionProof,
    RegisteredTool,
    TerminalRule,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolProvenance,
    VerificationPolicy,
    VerificationRecord,
    VerificationStatus,
)
from homemaster.tools.core_tools import build_core_tools
from homemaster.tools.file_tools import build_file_tools
from homemaster.tools.memory_tools import build_memory_tools
from homemaster.tools.observe import ScreenshotTool
from homemaster.tools.service_tools import build_service_tools
from homemaster.tools.web_tools import build_web_tools

_ROBOCASA_MUTATING_TOOLS = frozenset(
    {
        "robocasa_skill",
        "robocasa_arm",
        "robocasa_navigate",
        "robocasa_move_to",
        "robocasa_move_delta",
        "robocasa_rotate_pitch",
        "robocasa_set_gripper",
        "robocasa_release",
        "robocasa_scripted_grasp",
        "robocasa_move_base",
    }
)

_MEMORY_WRITE_TOOL_NAMES = frozenset(
    {
        "memory_writer",
        "mindmemos_add",
        "mindmemos_update",
        "mindmemos_delete",
        "mindmemos_feedback",
    }
)

_INTENTIONAL_COLLISIONS = {
    "observe": ("home", frozenset({"home", "alfworld"})),
    "task_planner": ("home", frozenset({"home", "alfworld"})),
    "task_progress_check": ("home", frozenset({"home", "alfworld"})),
}


class _ReceiptVerifier:
    def __init__(self, *, external_state: bool = False) -> None:
        self._external_state = external_state

    async def verify(
        self,
        result: ToolExecutionResult,
        context: ToolExecutionContext,
    ) -> VerificationRecord:
        refs = result.evidence_refs
        if not refs:
            return VerificationRecord(
                status=VerificationStatus.PENDING,
                detail="backend supplied no verifiable evidence reference",
            )
        if result.status.value != "success":
            return VerificationRecord(
                status=VerificationStatus.FAILED,
                detail="backend receipt reports failure",
                evidence_refs=refs,
            )
        if self._external_state:
            state = getattr(context.backend, "current_state", None)
            if state is not None and getattr(state, "won", None) is False:
                return VerificationRecord(
                    status=VerificationStatus.FAILED,
                    detail="external terminal state is not won",
                    evidence_refs=refs,
                )
        return VerificationRecord(
            status=VerificationStatus.PASSED,
            detail="typed backend receipt accepted",
            evidence_refs=refs,
        )


def build_tool_registry(
    *,
    environment: Literal["local_robot", "alfworld", "browser", "robocasa"] | None,
    memory_path: Path | None = None,
    runtime_memory_root: Path | None = None,
    memory_mode: str = "disabled",
    memory_enabled: bool = True,
    memory_tier: Literal["files", "full"] = "full",
) -> ToolRegistry:
    """Compose common tools with exactly one explicit environment tool set."""

    home_tools = _home_tools(
        memory_path=memory_path,
        runtime_memory_root=runtime_memory_root,
        memory_enabled=memory_enabled,
        memory_tier=memory_tier,
    )
    sources: dict[str, tuple[RegisteredTool, ...]] = {"home": home_tools}
    if environment == "alfworld":
        sources["alfworld"] = _alfworld_tools(
            memory_mode=memory_mode,
            memory_path=memory_path,
            runtime_memory_root=runtime_memory_root,
        )
    elif environment == "browser":
        pass
    elif environment == "robocasa":
        sources["robocasa"] = _robocasa_tools(
            memory_mode=memory_mode,
            memory_path=memory_path,
            runtime_memory_root=runtime_memory_root,
        )
    elif environment not in (None, "local_robot"):
        raise ValueError(f"unsupported tool environment: {environment}")

    selected = _select_universal_tools(sources)
    if environment == "robocasa":
        # The completion-claim guard for a borrowed benchmark environment:
        # ``task_progress_check`` arrives via the universal home source with a
        # NORMAL terminal rule, which would let the model end an episode on its
        # own say-so.  For RoboCasa the environment success flag must approve
        # completion, so the selected universal gets the external-owner rule —
        # same contract ALFWorld gives its environment-scoped twin.
        selected = tuple(
            replace(
                tool,
                definition=replace(
                    tool.definition,
                    verification_policy=replace(
                        tool.definition.verification_policy,
                        terminal_rule=TerminalRule.EXTERNAL_TERMINAL_OWNER,
                    ),
                ),
            )
            if tool.definition.model_alias == "task_progress_check"
            else tool
            for tool in selected
        )
    if environment == "robocasa" and memory_mode == "readonly":
        # ``readonly`` must be a real boundary for the memory ablation arm:
        # the mindmemos write tools arrive via the always-on home source, so
        # gating only inside _robocasa_tools would leave them callable.
        selected = tuple(
            tool
            for tool in selected
            if tool.definition.model_alias not in _MEMORY_WRITE_TOOL_NAMES
        )
    if environment == "browser":
        # Browser runs receive the run-owned V3.1 registry once a session
        # exists; the legacy generic screenshot tool must not leak into the
        # provider schema during profile construction.
        selected = tuple(tool for tool in selected if tool.definition.model_alias != "observe")
    registry = ToolRegistry()
    registry.register_many(from_registered_tool(tool) for tool in selected)
    return registry


def _select_universal_tools(
    sources: dict[str, tuple[RegisteredTool, ...]],
) -> tuple[RegisteredTool, ...]:
    candidates: dict[str, list[tuple[str, RegisteredTool]]] = {}
    for source, tools in sources.items():
        for tool in tools:
            candidates.setdefault(tool.definition.model_alias, []).append((source, tool))

    selected: list[RegisteredTool] = []
    for name, entries in candidates.items():
        if len(entries) == 1:
            selected.append(entries[0][1])
            continue
        source_names = [source for source, _tool in entries]
        collision = _INTENTIONAL_COLLISIONS.get(name)
        if (
            collision is None
            or len(source_names) != len(set(source_names))
            or not set(source_names).issubset(collision[1])
            or collision[0] not in source_names
        ):
            raise ValueError(
                f"unapproved duplicate tool name {name!r} from sources {source_names!r}"
            )
        winner = collision[0]
        selected.append(next(tool for source, tool in entries if source == winner))
    return tuple(selected)


def _home_tools(
    *,
    memory_path: Path | None,
    runtime_memory_root: Path | None,
    memory_enabled: bool = True,
    memory_tier: Literal["files", "full"] = "full",
) -> tuple[RegisteredTool, ...]:
    tools = [
        build_terminal_tool(),
        *build_core_tools(),
        *build_file_tools(),
        *(build_memory_tools(mode=memory_tier) if memory_enabled else ()),
        *build_web_tools(),
        *build_service_tools(),
    ]
    existing_names = {tool.definition.model_alias for tool in tools}
    tools_by_name = {
        tool.definition.model_alias: tool
        for tool in (
            make_task_interpreter(),
            make_target_grounder(),
            make_load_skill(),
            make_task_summarizer(),
            make_task_planner_tool(),
            make_task_progress_check_tool(),
        )
    }
    ordered = (
        "task_interpreter",
        "target_grounder",
        "load_skill",
        "observe",
        "task_summarizer",
        "task_planner",
        "task_progress_check",
    )
    for name in ordered:
        if name in existing_names:
            continue
        if name == "observe":
            tools.append(_screenshot_tool())
            continue
        tool = tools_by_name.get(name)
        if tool is None:
            continue
        tools.append(
            _configured_tool(
                tool,
                environment="homemaster" if name == "load_skill" else "home",
                policy=_policy_for(name, environment="home"),
                state_effects=(),
                required_capabilities=("tool.read",) if name == "load_skill" else (),
            )
        )
    return tuple(tools)


def _alfworld_tools(
    *,
    memory_mode: str,
    memory_path: Path | None,
    runtime_memory_root: Path | None,
) -> tuple[RegisteredTool, ...]:
    from homemaster.alfworld.tools import (
        make_alfworld_robot_go_to,
        make_alfworld_robot_manipulate,
        make_alfworld_robot_verify,
    )

    if memory_mode not in {"disabled", "readonly", "full"}:
        raise ValueError(f"unsupported memory_mode: {memory_mode}")
    specs: list[RegisteredTool] = []
    if memory_mode in {"readonly", "full"}:
        specs.append(make_memory_retriever(memory_path=memory_path))
    if memory_mode == "full":
        specs.append(make_memory_writer(runtime_memory_root=runtime_memory_root))
    specs.extend(
        (
            make_alfworld_robot_go_to(),
            make_alfworld_robot_manipulate(),
            make_alfworld_robot_verify(),
            make_task_planner_tool(),
            make_task_progress_check_tool(),
        )
    )
    tools = [
        _configured_tool(
            tool,
            environment="alfworld",
            policy=_policy_for(tool.definition.model_alias, environment="alfworld"),
            state_effects=("backend.advance",)
            if tool.definition.model_alias in {"robot_go_to", "robot_manipulate"}
            else (),
        )
        for tool in specs
    ]
    tools.append(_screenshot_tool())
    return tuple(tools)


def _robocasa_tools(
    *,
    memory_mode: str,
    memory_path: Path | None,
    runtime_memory_root: Path | None,
) -> tuple[RegisteredTool, ...]:
    from homemaster.robocasa.tools import build_robocasa_tools

    if memory_mode not in {"disabled", "readonly", "full"}:
        raise ValueError(f"unsupported memory_mode: {memory_mode}")
    specs: list[RegisteredTool] = []
    if memory_mode in {"readonly", "full"}:
        specs.append(make_memory_retriever(memory_path=memory_path))
    if memory_mode == "full":
        specs.append(make_memory_writer(runtime_memory_root=runtime_memory_root))
    specs.extend(build_robocasa_tools())
    return tuple(
        _configured_tool(
            tool,
            environment="robocasa",
            policy=_policy_for(tool.definition.model_alias, environment="robocasa"),
            state_effects=("backend.advance",)
            if tool.definition.model_alias in _ROBOCASA_MUTATING_TOOLS
            else (),
        )
        for tool in specs
    )


def alfworld_physical_manipulate_tool(*, backend: Any) -> Any:
    """Build the opt-in permission-gated ALFWorld manipulation tool.

    The benchmark profile can opt into the permission-gated physical tools by
    supplying the current Harness-backed AlfworldBackend.
    """
    from homemaster.alfworld.permission_adapter import (
        AlfworldPermissionAdapter,
    )
    from homemaster.alfworld.tools import (
        make_alfworld_robot_manipulate,
    )

    spec = make_alfworld_robot_manipulate()
    adapter = AlfworldPermissionAdapter(tool="robot_manipulate", backend=backend)
    registered = _configured_tool(
        spec,
        environment="alfworld",
        policy=_policy_for("robot_manipulate", environment="alfworld"),
        state_effects=("backend.advance",),
    )
    return from_registered_tool(
        registered, physical=True, physical_adapter=adapter
    )


def alfworld_physical_go_to_tool(*, backend: Any) -> Any:
    from homemaster.alfworld.permission_adapter import AlfworldPermissionAdapter
    from homemaster.alfworld.tools import make_alfworld_robot_go_to
    spec = make_alfworld_robot_go_to()
    adapter = AlfworldPermissionAdapter(tool="robot_go_to", backend=backend)
    registered = _configured_tool(
        spec, environment="alfworld",
        policy=_policy_for("robot_go_to", environment="alfworld"),
        state_effects=("backend.advance",),
    )
    return from_registered_tool(registered, physical=True, physical_adapter=adapter)


def _screenshot_tool() -> RegisteredTool:
    return RegisteredTool(
        definition=ToolDefinition(
            internal_id="homemaster.observe.v1",
            model_alias="observe",
            description="Capture the current visual frame for inspection.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            output_schema={"type": "object", "properties": {}, "additionalProperties": False},
            verification_policy=VerificationPolicy(execution_proof=ExecutionProof.NONE),
            provenance=ToolProvenance(source="homemaster", reference="homemaster.observe"),
            version="1.9.0",
        ),
        executor=ScreenshotTool(),
    )


def _configured_tool(
    registered: RegisteredTool,
    *,
    environment: str,
    policy: VerificationPolicy,
    state_effects: tuple[str, ...],
    required_capabilities: tuple[str, ...] = (),
) -> RegisteredTool:
    definition = registered.definition
    definition = replace(
        definition,
        provenance=ToolProvenance(
            source=environment,
            reference=f"{environment}.{definition.model_alias}",
        ),
        version="3.5.0",
        verification_policy=policy,
        state_effects=state_effects,
        concurrency_policy=(
            ConcurrencyPolicy.RESOURCE_KEY if state_effects else ConcurrencyPolicy.PARALLEL
        ),
        resource_key=f"{environment}:backend" if state_effects else None,
        required_capabilities=required_capabilities,
    )
    verifier = None
    if policy.execution_proof is not ExecutionProof.NONE:
        verifier = _ReceiptVerifier(
            external_state=policy.execution_proof is ExecutionProof.EXTERNAL_STATE
        )
    return replace(
        registered,
        definition=definition,
        verifier=verifier,
    )


def _policy_for(name: str, *, environment: str) -> VerificationPolicy:
    if name == "task_progress_check" and environment == "alfworld":
        return VerificationPolicy(
            execution_proof=ExecutionProof.NONE,
            terminal_rule=TerminalRule.EXTERNAL_TERMINAL_OWNER,
        )
    receipt_tools = {
        "robot_manipulate",
        "robot_go_to",
    }
    if environment == "robocasa":
        if name == "robocasa_verify":
            return VerificationPolicy(execution_proof=ExecutionProof.EXTERNAL_STATE)
        if name.startswith("robocasa_") and name != "robocasa_observe":
            if name in {"robocasa_back_project", "robocasa_world_map"}:
                return VerificationPolicy(execution_proof=ExecutionProof.NONE)
            return VerificationPolicy(execution_proof=ExecutionProof.STRUCTURED_RECEIPT)
        return VerificationPolicy(execution_proof=ExecutionProof.NONE)
    if name in receipt_tools:
        return VerificationPolicy(execution_proof=ExecutionProof.STRUCTURED_RECEIPT)
    if name == "robot_verify":
        return VerificationPolicy(execution_proof=ExecutionProof.EXTERNAL_STATE)
    return VerificationPolicy(execution_proof=ExecutionProof.NONE)
__all__ = [
    "build_tool_registry",
]
