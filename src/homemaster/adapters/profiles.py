"""Canonical model-facing profiles for Home, ALFWorld, and Coworker.

The benchmark modules continue to own their borrowed backends and scoring
logic.  This adapter owns only the stable Catalog/View projection and the
explicit ``observe`` capability shared by all three environments.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from homemaster.agent.normalized import RunContext
from homemaster.benchmarking.alfworld.tools import (
    make_alfworld_robot_go_to,
    make_alfworld_robot_manipulate,
    make_alfworld_robot_verify,
)
from homemaster.benchmarking.coworker_demo.browser_tools import browser_tool_specs
from homemaster.benchmarking.coworker_demo.correlation import correlated_action_id
from homemaster.benchmarking.coworker_demo.decision_tools import make_sop_decide
from homemaster.benchmarking.coworker_demo.environment_client import EnvironmentClient
from homemaster.benchmarking.coworker_demo.terminal_tools import make_terminal_execute
from homemaster.domain.tools import (
    make_memory_retriever,
    make_memory_writer,
    make_robot_manipulate,
    make_robot_navigate,
    make_robot_verify,
    make_skill,
    make_skill_view,
    make_target_grounder,
    make_task_interpreter,
    make_task_summarizer,
)
from homemaster.task_state.tools import make_task_planner_tool, make_task_progress_check_tool
from homemaster.tools.catalog import ToolCatalog, ToolView
from homemaster.tools.contracts import (
    ConcurrencyPolicy,
    ExecutionProof,
    RegisteredTool,
    TerminalRule,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
    VerificationRecord,
    VerificationStatus,
)
from homemaster.tools.legacy_adapter import adapt_legacy_tool_spec
from homemaster.tools.observe import ScreenshotTool
from homemaster.tools.openharness_bash import build_bash_tool
from homemaster.tools.openharness_core import build_core_tools
from homemaster.tools.openharness_files import build_file_tools
from homemaster.tools.openharness_services import build_service_tools
from homemaster.tools.openharness_web import build_web_tools


@dataclass(frozen=True)
class EnvironmentToolProfile:
    environment: str
    catalog: ToolCatalog
    view: ToolView

    @property
    def enabled_tool_ids(self) -> tuple[str, ...]:
        return self.view.enabled_tool_ids

    @property
    def model_tool_names(self) -> tuple[str, ...]:
        return tuple(manifest["name"] for manifest in self.view.manifests())  # type: ignore[misc]

    def manifests(self) -> tuple[dict[str, object], ...]:
        return self.view.manifests()


@dataclass
class CoworkerScreenshotBackend:
    """Thread-owned bridge from the canonical screenshot tool to Playwright."""

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
        if result.status is not ToolExecutionStatus.SUCCESS:
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


def build_home_profile(
    *,
    catalog: ToolCatalog | None = None,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    runtime_memory_root: Path | None = None,
) -> EnvironmentToolProfile:
    catalog = catalog or ToolCatalog()
    bash_tool = build_bash_tool()
    if catalog.get(bash_tool.definition.internal_id) is None:
        catalog.register(bash_tool)
    for tool in build_core_tools():
        if catalog.get(tool.definition.internal_id) is None:
            catalog.register(tool)
    for tool in build_file_tools():
        if catalog.get(tool.definition.internal_id) is None:
            catalog.register(tool)
    for tool in build_web_tools():
        if catalog.get(tool.definition.internal_id) is None:
            catalog.register(tool)
    for tool in build_service_tools():
        if catalog.get(tool.definition.internal_id) is None:
            catalog.register(tool)
    specs = {
        spec.name: spec
        for spec in (
            make_task_interpreter(),
            make_memory_retriever(memory_path=memory_path),
            make_target_grounder(world_path=world_path),
            make_skill(),
            make_skill_view(),
            make_robot_navigate(),
            make_robot_manipulate(),
            make_robot_verify(),
            make_memory_writer(runtime_memory_root=runtime_memory_root),
            make_task_summarizer(),
            make_task_planner_tool(),
            make_task_progress_check_tool(),
        )
    }
    ordered = [
        "task_interpreter",
        "memory_retriever",
        "target_grounder",
        "bash",
        "brief",
        "sleep",
        "tool_search",
        "read_file",
        "write_file",
        "edit_file",
        "notebook_edit",
        "glob",
        "grep",
        "web_fetch",
        "web_search",
        "todo_write",
        "enter_worktree",
        "exit_worktree",
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
        "skill",
        "skill_view",
        "robot_go_to",
        "observe",
        "robot_manipulate",
        "robot_verify",
        "memory_writer",
        "task_summarizer",
        "task_planner",
        "task_progress_check",
    ]
    for name in ordered:
        if name in {
            "bash",
            "brief",
            "sleep",
            "tool_search",
            "read_file",
            "write_file",
            "edit_file",
            "notebook_edit",
            "glob",
            "grep",
            "web_fetch",
            "web_search",
            "todo_write",
            "enter_worktree",
            "exit_worktree",
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
        }:
            continue
        if name == "robot_go_to":
            spec = specs.get("robot_navigate")
            assert spec is not None
            _register_adapted(
                catalog,
                spec,
                internal_id="home.robot_go_to.v1",
                alias="robot_go_to",
                environment="home",
                policy=VerificationPolicy(execution_proof=ExecutionProof.STRUCTURED_RECEIPT),
                state_effects=("backend.advance",),
            )
        elif name == "observe":
            _register_screenshot(catalog)
        else:
            spec = specs.get(name)
            if spec is None:
                continue
            policy = _policy_for(name, environment="home")
            _register_adapted(
                catalog,
                spec,
                internal_id=("openharness.skill.v1" if name == "skill" else f"home.{name}.v1"),
                alias=name,
                environment=("openharness" if name == "skill" else "home"),
                policy=policy,
                state_effects=("backend.advance",) if name == "robot_manipulate" else (),
                required_capabilities=("tool.read",) if name == "skill" else (),
            )
    return _profile(
        catalog,
        "home",
        [
            "core.observe.v1"
            if name == "observe"
            else f"openharness.{name}.v1"
            if name
            in {
                "bash",
                "brief",
                "sleep",
                "tool_search",
                "read_file",
                "write_file",
                "edit_file",
                "notebook_edit",
                "glob",
                "grep",
                "web_fetch",
                "web_search",
                "todo_write",
                "enter_worktree",
                "exit_worktree",
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
            }
            else "openharness.skill.v1"
            if name == "skill"
            else f"home.{name}.v1"
            for name in ordered
        ],
    )


def build_alfworld_profile(
    *,
    catalog: ToolCatalog | None = None,
    memory_mode: str = "disabled",
    memory_path: Path | None = None,
    runtime_memory_root: Path | None = None,
) -> EnvironmentToolProfile:
    catalog = catalog or ToolCatalog()
    if memory_mode not in {"disabled", "readonly", "full"}:
        raise ValueError(f"unsupported memory_mode: {memory_mode}")
    legacy_specs = []
    if memory_mode in {"readonly", "full"}:
        legacy_specs.append(make_memory_retriever(memory_path=memory_path))
    if memory_mode == "full":
        legacy_specs.append(make_memory_writer(runtime_memory_root=runtime_memory_root))
    legacy_specs.extend(
        [
            make_alfworld_robot_go_to(),
            make_alfworld_robot_manipulate(),
            make_alfworld_robot_verify(),
            make_task_planner_tool(),
            make_task_progress_check_tool(),
        ]
    )
    specs = {spec.name: spec for spec in legacy_specs}
    ordered: list[str] = [
        "observe",
        *(spec.name for spec in legacy_specs),
    ]
    for name in ordered[1:]:
        spec = specs.get(name)
        assert spec is not None
        _register_adapted(
            catalog,
            spec,
            internal_id=f"alfworld.{name}.v1",
            alias=name,
            environment="alfworld",
            policy=_policy_for(name, environment="alfworld"),
            state_effects=(
                ("backend.advance",) if name in {"robot_go_to", "robot_manipulate"} else ()
            ),
        )
    _register_screenshot(catalog)
    return _profile(
        catalog,
        "alfworld",
        ["core.observe.v1" if name == "observe" else f"alfworld.{name}.v1" for name in ordered],
    )


def build_coworker_profile(
    *,
    catalog: ToolCatalog | None = None,
) -> EnvironmentToolProfile:
    catalog = catalog or ToolCatalog()
    task_planner = _coworker_task_tool(make_task_planner_tool(), planner=True)
    task_progress = _coworker_task_tool(make_task_progress_check_tool(), planner=False)
    skill_view = _coworker_skill_view(make_skill_view())
    legacy_specs = {
        spec.name: spec
        for spec in (
            task_planner,
            task_progress,
            skill_view,
            *browser_tool_specs(),
            make_terminal_execute(),
            make_sop_decide(),
        )
    }
    ordered = [
        "task_planner",
        "task_progress_check",
        "skill_view",
        "browser_navigate",
        "observe",
        "browser_click",
        "browser_fill",
        "browser_select",
        "browser_wait",
        "terminal_execute",
        "sop_decide",
    ]
    for name in ordered:
        if name == "observe":
            _register_screenshot(catalog)
            continue
        spec = legacy_specs.get(name)
        assert spec is not None
        _register_adapted(
            catalog,
            spec,
            internal_id=f"coworker.{name}.v1",
            alias=name,
            environment="coworker",
            policy=_policy_for(name, environment="coworker"),
            state_effects=("browser.advance",)
            if name in {"browser_navigate", "browser_click", "browser_fill", "browser_select"}
            else (),
        )
    return _profile(
        catalog,
        "coworker",
        ["core.observe.v1" if name == "observe" else f"coworker.{name}.v1" for name in ordered],
    )


def _coworker_skill_view(spec: Any) -> Any:
    original = spec.executor
    schema = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "enum": ["change_execution", "evidence_discipline"],
                "description": "Name of one available coworker skill to view.",
            }
        },
        "required": ["skill_name"],
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
        state = client.state(run_context.run_id)
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
            run_context.run_id,
            action_id=correlated_action_id(run_context),
            tool_name=spec.name,
            arguments=arguments,
            node_id=node_id,
        )
        if isinstance(result.data, dict):
            result.data["coworker_evidence_refs"] = [mirrored["event"]["event_id"]]
        return result

    return spec.model_copy(update={"executor": executor})


def build_environment_profiles(
    *,
    memory_mode: str = "disabled",
) -> dict[str, EnvironmentToolProfile]:
    catalog = ToolCatalog()
    return {
        "home": build_home_profile(catalog=catalog),
        "alfworld": build_alfworld_profile(
            catalog=catalog,
            memory_mode=memory_mode,
        ),
        "coworker": build_coworker_profile(catalog=catalog),
    }


def _register_screenshot(catalog: ToolCatalog) -> None:
    internal_id = "core.observe.v1"
    if catalog.get(internal_id) is not None:
        return
    definition = ToolDefinition(
        internal_id=internal_id,
        model_alias="observe",
        description="Capture the current visual frame for inspection.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema={"type": "object", "properties": {}, "additionalProperties": False},
        verification_policy=VerificationPolicy(execution_proof=ExecutionProof.NONE),
        provenance=ToolProvenance(source="core", reference="core.observe"),
        version="1.9.0",
    )
    catalog.register(
        RegisteredTool(
            definition=definition,
            executor=ScreenshotTool(),
        )
    )


def _register_adapted(
    catalog: ToolCatalog,
    spec: Any,
    *,
    internal_id: str,
    alias: str,
    environment: str,
    policy: VerificationPolicy,
    state_effects: tuple[str, ...],
    required_capabilities: tuple[str, ...] = (),
) -> None:
    if catalog.get(internal_id) is not None:
        return
    adapted = adapt_legacy_tool_spec(
        spec,
        internal_id=internal_id,
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
    )
    catalog.register(
        RegisteredTool(
            definition=definition,
            executor=adapted.registered_tool.executor,
            verifier=_ReceiptVerifier(
                external_state=policy.execution_proof is ExecutionProof.EXTERNAL_STATE
            )
            if policy.execution_proof is not ExecutionProof.NONE
            else None,
        )
    )


def _policy_for(name: str, *, environment: str) -> VerificationPolicy:
    if name == "task_progress_check" and environment in {"alfworld", "coworker"}:
        return VerificationPolicy(
            execution_proof=ExecutionProof.NONE,
            terminal_rule=TerminalRule.EXTERNAL_TERMINAL_OWNER,
        )
    if name in {"robot_manipulate", "robot_go_to"}:
        return VerificationPolicy(execution_proof=ExecutionProof.STRUCTURED_RECEIPT)
    if name in {"robot_verify"}:
        return VerificationPolicy(execution_proof=ExecutionProof.EXTERNAL_STATE)
    if name == "browser_navigate":
        return VerificationPolicy(execution_proof=ExecutionProof.STRUCTURED_RECEIPT)
    if name in {"browser_click", "browser_fill", "browser_select", "browser_wait"}:
        return VerificationPolicy(execution_proof=ExecutionProof.STRUCTURED_RECEIPT)
    if name in {
        "task_planner",
        "task_progress_check",
        "skill",
        "skill_view",
        "memory_retriever",
        "memory_writer",
        "task_summarizer",
        "task_interpreter",
        "target_grounder",
    }:
        return VerificationPolicy(execution_proof=ExecutionProof.NONE)
    if name == "sop_decide" and environment == "coworker":
        return VerificationPolicy(execution_proof=ExecutionProof.STRUCTURED_RECEIPT)
    return VerificationPolicy(execution_proof=ExecutionProof.STRUCTURED_RECEIPT)


def _profile(catalog: ToolCatalog, environment: str, ids: list[str]) -> EnvironmentToolProfile:
    enabled = [internal_id for internal_id in ids if catalog.get(internal_id) is not None]
    return EnvironmentToolProfile(environment, catalog, catalog.freeze(enabled))


__all__ = [
    "CoworkerScreenshotBackend",
    "EnvironmentToolProfile",
    "build_alfworld_profile",
    "build_coworker_profile",
    "build_environment_profiles",
    "build_home_profile",
]
