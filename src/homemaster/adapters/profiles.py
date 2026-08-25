"""Compose the one ordinary-name tool Registry used by every environment."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from homemaster.agent.normalized import RunContext
from homemaster.benchmarking.alfworld.tools import (
    make_alfworld_robot_go_to,
    make_alfworld_robot_manipulate,
    make_alfworld_robot_verify,
)
from homemaster.benchmarking.coworker_demo.browser_tools import browser_tool_specs
from homemaster.benchmarking.coworker_demo.correlation import (
    correlated_action_id,
    coworker_domain_run_id,
)
from homemaster.benchmarking.coworker_demo.decision_tools import make_sop_decide
from homemaster.benchmarking.coworker_demo.environment_client import EnvironmentClient
from homemaster.benchmarking.coworker_demo.terminal_tools import make_terminal_execute
from homemaster.domain.tools import (
    make_load_skill,
    make_memory_retriever,
    make_memory_writer,
    make_robot_manipulate,
    make_robot_navigate,
    make_robot_verify,
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
from homemaster.tools.legacy_adapter import adapt_legacy_tool_spec
from homemaster.tools.memory_tools import build_memory_tools
from homemaster.tools.observe import ScreenshotTool
from homemaster.tools.service_tools import build_service_tools
from homemaster.tools.web_tools import build_web_tools

_INTENTIONAL_COLLISIONS = {
    "load_skill": ("home", frozenset({"home", "coworker"})),
    "robot_go_to": ("alfworld", frozenset({"home", "alfworld"})),
    "observe": ("home", frozenset({"home", "alfworld", "coworker"})),
    "robot_manipulate": ("alfworld", frozenset({"home", "alfworld"})),
    "robot_verify": ("alfworld", frozenset({"home", "alfworld"})),
    "task_planner": ("home", frozenset({"home", "alfworld", "coworker"})),
    "task_progress_check": ("home", frozenset({"home", "alfworld", "coworker"})),
}


@dataclass
class CoworkerScreenshotBackend:
    """Thread-owned bridge from the screenshot tool to Playwright."""

    driver: Any
    domain_run_id: str
    generation: int = 0

    @property
    def backend_id(self) -> str:
        return f"coworker:{self.domain_run_id}"

    def screenshot(self) -> bytes:
        return self.driver.screenshot()

    def bind_application_run(self, run_id: str, generation: int) -> None:
        del run_id
        self.generation = generation


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


def build_universal_tool_registry(
    *,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    runtime_memory_root: Path | None = None,
    memory_mode: str = "disabled",
    memory_enabled: bool = True,
) -> ToolRegistry:
    """Compatibility builder for the historical local-robot CLI surface."""

    return build_tool_registry(
        environment="local_robot",
        world_path=world_path,
        memory_path=memory_path,
        runtime_memory_root=runtime_memory_root,
        memory_mode=memory_mode,
        memory_enabled=memory_enabled,
    )


def build_tool_registry(
    *,
    environment: Literal["local_robot", "alfworld", "coworker", "browser"] | None,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    runtime_memory_root: Path | None = None,
    memory_mode: str = "disabled",
    memory_enabled: bool = True,
) -> ToolRegistry:
    """Compose common tools with exactly one explicit environment tool set."""

    home_tools = _home_tools(
        world_path=world_path,
        memory_path=memory_path,
        runtime_memory_root=runtime_memory_root,
        memory_enabled=memory_enabled,
    )
    robot_names = {"robot_go_to", "robot_manipulate", "robot_verify"}
    sources: dict[str, tuple[RegisteredTool, ...]] = {
        "home": tuple(tool for tool in home_tools if tool.definition.model_alias not in robot_names)
    }
    if environment == "local_robot":
        sources["local_robot"] = tuple(
            tool for tool in home_tools if tool.definition.model_alias in robot_names
        )
    elif environment == "alfworld":
        sources["alfworld"] = _alfworld_tools(
            memory_mode=memory_mode,
            memory_path=memory_path,
            runtime_memory_root=runtime_memory_root,
        )
    elif environment == "coworker":
        sources["coworker"] = _coworker_tools()
    elif environment == "browser":
        pass
    elif environment is not None:
        raise ValueError(f"unsupported tool environment: {environment}")

    selected = _select_universal_tools(sources)
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
    world_path: Path | None,
    memory_path: Path | None,
    runtime_memory_root: Path | None,
    memory_enabled: bool = True,
) -> tuple[RegisteredTool, ...]:
    tools = [
        build_terminal_tool(),
        *build_core_tools(),
        *build_file_tools(),
        *(build_memory_tools() if memory_enabled else ()),
        *build_web_tools(),
        *build_service_tools(),
    ]
    existing_names = {tool.definition.model_alias for tool in tools}
    specs = {
        spec.name: spec
        for spec in (
            make_task_interpreter(),
            make_target_grounder(world_path=world_path),
            make_load_skill(),
            make_robot_navigate(),
            make_robot_manipulate(),
            make_robot_verify(),
            make_task_summarizer(),
            make_task_planner_tool(),
            make_task_progress_check_tool(),
        )
    }
    ordered = (
        "task_interpreter",
        "target_grounder",
        "load_skill",
        "robot_go_to",
        "observe",
        "robot_manipulate",
        "robot_verify",
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
        spec = specs.get("robot_navigate" if name == "robot_go_to" else name)
        if spec is None:
            continue
        tools.append(
            _adapted_tool(
                spec,
                alias=name,
                environment="homemaster" if name == "load_skill" else "home",
                policy=_policy_for(name, environment="home"),
                state_effects=("backend.advance",)
                if name in {"robot_go_to", "robot_manipulate"}
                else (),
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
    if memory_mode not in {"disabled", "readonly", "full"}:
        raise ValueError(f"unsupported memory_mode: {memory_mode}")
    specs = []
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
        _adapted_tool(
            spec,
            alias=spec.name,
            environment="alfworld",
            policy=_policy_for(spec.name, environment="alfworld"),
            state_effects=("backend.advance",)
            if spec.name in {"robot_go_to", "robot_manipulate"}
            else (),
        )
        for spec in specs
    ]
    tools.append(_screenshot_tool())
    return tuple(tools)


def _coworker_tools() -> tuple[RegisteredTool, ...]:
    specs = (
        _coworker_task_tool(make_task_planner_tool(), planner=True),
        _coworker_task_tool(make_task_progress_check_tool(), planner=False),
        _coworker_load_skill(make_load_skill()),
        *browser_tool_specs(),
        make_terminal_execute(),
        make_sop_decide(),
    )
    tools = [
        _adapted_tool(
            spec,
            alias=spec.name,
            environment="coworker",
            policy=_policy_for(spec.name, environment="coworker"),
            state_effects=("browser.advance",)
            if spec.name in {"browser_navigate", "browser_click", "browser_fill", "browser_select"}
            else (),
        )
        for spec in specs
    ]
    tools.append(_screenshot_tool())
    return tuple(tools)


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


def _adapted_tool(
    spec: Any,
    *,
    alias: str,
    environment: str,
    policy: VerificationPolicy,
    state_effects: tuple[str, ...],
    required_capabilities: tuple[str, ...] = (),
) -> RegisteredTool:
    adapted = adapt_legacy_tool_spec(
        spec,
        internal_id=f"homemaster.{alias}.v1",
        version="1.9.0",
        provenance=ToolProvenance(source=environment, reference=f"{environment}.{spec.name}"),
        output_schema=getattr(spec, "output_schema", None) or {"type": "object"},
    )
    definition = replace(
        adapted.definition,
        model_alias=alias,
        verification_policy=policy,
        state_effects=state_effects,
        concurrency_policy=(
            ConcurrencyPolicy.RESOURCE_KEY if state_effects else ConcurrencyPolicy.PARALLEL
        ),
        resource_key=f"{environment}:backend" if state_effects else None,
        required_capabilities=required_capabilities,
        requires_model_observation=(
            environment == "alfworld" and alias in {"robot_go_to", "robot_manipulate"}
        ),
    )
    verifier = None
    if policy.execution_proof is not ExecutionProof.NONE:
        verifier = _ReceiptVerifier(
            external_state=policy.execution_proof is ExecutionProof.EXTERNAL_STATE
        )
    return RegisteredTool(
        definition=definition,
        executor=adapted.registered_tool.executor,
        verifier=verifier,
    )


def _policy_for(name: str, *, environment: str) -> VerificationPolicy:
    if name == "task_progress_check" and environment in {"alfworld", "coworker"}:
        return VerificationPolicy(
            execution_proof=ExecutionProof.NONE,
            terminal_rule=TerminalRule.EXTERNAL_TERMINAL_OWNER,
        )
    receipt_tools = {
        "robot_manipulate",
        "robot_go_to",
        "browser_navigate",
        "browser_click",
        "browser_fill",
        "browser_select",
        "browser_wait",
        "sop_decide",
    }
    if name in receipt_tools:
        return VerificationPolicy(execution_proof=ExecutionProof.STRUCTURED_RECEIPT)
    if name == "robot_verify":
        return VerificationPolicy(execution_proof=ExecutionProof.EXTERNAL_STATE)
    return VerificationPolicy(execution_proof=ExecutionProof.NONE)


def _coworker_load_skill(spec: Any) -> Any:
    original = spec.executor
    schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "enum": ["change_execution", "evidence_discipline"],
                "description": "Name of one available Coworker Skill to load.",
            }
        },
        "required": ["name"],
        "additionalProperties": False,
    }

    def executor(*, arguments: dict[str, Any], run_context: RunContext):
        if original is None:
            raise RuntimeError(f"{spec.name} has no executor")
        run_context.deps["coworker_budget"].before_external(run_context.deps["coworker_outcome"])
        return original(arguments=arguments, run_context=run_context)

    return spec.model_copy(update={"input_schema": schema, "executor": executor})


def _coworker_task_tool(spec: Any, *, planner: bool) -> Any:
    original = spec.executor

    def executor(*, arguments: dict[str, Any], run_context: RunContext):
        if original is None:
            raise RuntimeError(f"{spec.name} has no executor")
        run_context.deps["coworker_budget"].before_external(run_context.deps["coworker_outcome"])
        result = original(arguments=arguments, run_context=run_context)
        client: EnvironmentClient = run_context.deps["coworker_environment"]
        domain_run_id = coworker_domain_run_id(run_context)
        state = client.state(domain_run_id)
        if planner:
            node_id = "PLAN_CREATED"
        elif state["phase"] == "ready_to_change":
            node_id = "PRE_PROGRESS"
        elif state["phase"] == "change_applied":
            node_id = "NORMAL_PROGRESS" if state.get("business_verified") else "IMPLEMENT_PROGRESS"
        elif state["phase"] == "rollback_submitted":
            node_id = "ROLLBACK_PROGRESS"
        else:
            node_id = None
        mirrored = client.runtime_event(
            domain_run_id,
            action_id=correlated_action_id(run_context),
            tool_name=spec.name,
            arguments=arguments,
            node_id=node_id,
        )
        if isinstance(result.data, dict):
            result.data["coworker_evidence_refs"] = [mirrored["event"]["event_id"]]
        return result

    return spec.model_copy(update={"executor": executor})


__all__ = [
    "CoworkerScreenshotBackend",
    "build_tool_registry",
    "build_universal_tool_registry",
]
