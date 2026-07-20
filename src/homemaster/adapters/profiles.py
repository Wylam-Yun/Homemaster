"""Canonical model-facing profiles for Home, ALFWorld, and Coworker.

The benchmark modules continue to own their borrowed backends and scoring
logic.  This adapter owns only the stable Catalog/View projection and the
explicit ``observe`` capability shared by all three environments.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from homemaster.benchmarking.alfworld.registry import build_alfworld_tool_registry
from homemaster.benchmarking.coworker_demo.registry import build_coworker_tool_registry
from homemaster.domain.tool_registry import build_home_tool_registry
from homemaster.observations import ObservationService
from homemaster.tools.catalog import ToolCatalog, ToolView
from homemaster.tools.contracts import (
    ExecutionProof,
    ObservationReference,
    PostActionObservation,
    RegisteredTool,
    ResultImage,
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

_OBSERVATION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "observation_id": {"type": "string"},
        "internal_tool_id": {"type": "string"},
        "backend_id": {"type": "string"},
        "run_id": {"type": "string"},
        "generation": {"type": "integer", "minimum": 0},
        "state_sequence": {"type": "integer", "minimum": 0},
        "capture_event_sequence": {"type": "integer", "minimum": 0},
        "media_type": {"type": "string"},
        "content_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "pixel_sha256": {
            "anyOf": [
                {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                {"type": "null"},
            ]
        },
        "evidence_ref": {"type": "string"},
    },
    "required": [
        "observation_id",
        "internal_tool_id",
        "backend_id",
        "run_id",
        "generation",
        "state_sequence",
        "capture_event_sequence",
        "media_type",
        "content_sha256",
        "pixel_sha256",
        "evidence_ref",
    ],
    "additionalProperties": False,
}


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


class _ObservationExecutor:
    def __init__(self, service: ObservationService) -> None:
        self._service = service

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        del arguments
        record = await self._service.capture_for_model(context)
        reference = ObservationReference(
            observation_id=record.observation_id,
            evidence_ref=record.evidence_ref,
            content_sha256=record.content_sha256,
        )
        image: ResultImage | None = None
        text = ""
        if record.is_raster:
            import base64

            image = ResultImage(
                media_type=record.media_type,
                data_base64=base64.b64encode(record.content_bytes).decode("ascii"),
                content_sha256=record.content_sha256,
                pixel_sha256=record.pixel_sha256,
                observation_id=record.observation_id,
            )
        else:
            text = record.content_bytes.decode("utf-8")
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text=text,
            data=record.to_dict(),
            images=(image,) if image is not None else (),
            observations=(reference,),
            evidence_refs=(record.evidence_ref,),
            backend_attempted=False,
        )


class _ReceiptVerifier:
    async def verify(
        self,
        result: ToolExecutionResult,
        context: ToolExecutionContext,
    ) -> VerificationRecord:
        refs = result.evidence_refs or (f"verification/{context.tool_call_id}",)
        return VerificationRecord(
            status=VerificationStatus.PASSED,
            detail="typed backend receipt accepted",
            evidence_refs=refs,
        )


def build_home_profile(
    *,
    catalog: ToolCatalog | None = None,
    observation_service: ObservationService | None = None,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    runtime_memory_root: Path | None = None,
) -> EnvironmentToolProfile:
    catalog = catalog or ToolCatalog()
    service = observation_service or ObservationService()
    legacy = build_home_tool_registry(
        world_path=world_path,
        memory_path=memory_path,
        runtime_memory_root=runtime_memory_root,
    )
    specs = {
        name: legacy.get(name)
        for name in legacy.all_names()
        if name not in {"robot_navigate", "robot_observe"}
    }
    ordered = [
        "task_interpreter",
        "memory_retriever",
        "target_grounder",
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
        if name == "robot_go_to":
            spec = legacy.get("robot_navigate")
            assert spec is not None
            _register_adapted(
                catalog,
                spec,
                internal_id="home.robot_go_to.v1",
                alias="robot_go_to",
                environment="home",
                policy=VerificationPolicy(
                    execution_proof=ExecutionProof.STRUCTURED_RECEIPT,
                    post_action_observation=PostActionObservation.FRESH_AFTER_BACKEND_ADVANCE,
                ),
                state_effects=("backend.advance",),
            )
        elif name == "observe":
            _register_observation(catalog, "home", service)
        else:
            spec = specs.get(name)
            if spec is None:
                continue
            policy = _policy_for(name, environment="home")
            _register_adapted(
                catalog,
                spec,
                internal_id=f"home.{name}.v1",
                alias=name,
                environment="home",
                policy=policy,
                state_effects=("backend.advance",) if name == "robot_manipulate" else (),
            )
    return _profile(catalog, "home", [f"home.{name}.v1" for name in ordered])


def build_alfworld_profile(
    *,
    catalog: ToolCatalog | None = None,
    observation_service: ObservationService | None = None,
    memory_mode: str = "disabled",
    memory_path: Path | None = None,
    runtime_memory_root: Path | None = None,
) -> EnvironmentToolProfile:
    catalog = catalog or ToolCatalog()
    service = observation_service or ObservationService()
    legacy = build_alfworld_tool_registry(
        memory_mode=memory_mode,
        memory_path=memory_path,
        runtime_memory_root=runtime_memory_root,
    )
    ordered: list[str] = ["observe", *legacy.all_names()]
    for name in legacy.all_names():
        spec = legacy.get(name)
        assert spec is not None
        _register_adapted(
            catalog,
            spec,
            internal_id=f"alfworld.{name}.v1",
            alias=name,
            environment="alfworld",
            policy=_policy_for(name, environment="alfworld"),
            state_effects=(
                ("backend.advance",)
                if name in {"robot_go_to", "robot_manipulate"}
                else ()
            ),
        )
    _register_observation(catalog, "alfworld", service)
    return _profile(catalog, "alfworld", [f"alfworld.{name}.v1" for name in ordered])


def build_coworker_profile(
    *,
    catalog: ToolCatalog | None = None,
    observation_service: ObservationService | None = None,
) -> EnvironmentToolProfile:
    catalog = catalog or ToolCatalog()
    service = observation_service or ObservationService()
    legacy = build_coworker_tool_registry()
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
            _register_observation(catalog, "coworker", service)
            continue
        spec = legacy.get(name)
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
    return _profile(catalog, "coworker", [f"coworker.{name}.v1" for name in ordered])


def build_environment_profiles(
    *,
    observation_service: ObservationService | None = None,
    memory_mode: str = "disabled",
) -> dict[str, EnvironmentToolProfile]:
    catalog = ToolCatalog()
    service = observation_service or ObservationService()
    return {
        "home": build_home_profile(catalog=catalog, observation_service=service),
        "alfworld": build_alfworld_profile(
            catalog=catalog,
            observation_service=service,
            memory_mode=memory_mode,
        ),
        "coworker": build_coworker_profile(catalog=catalog, observation_service=service),
    }


def _register_observation(
    catalog: ToolCatalog,
    environment: str,
    service: ObservationService,
) -> None:
    internal_id = f"{environment}.observe.v1"
    if catalog.get(internal_id) is not None:
        return
    definition = ToolDefinition(
        internal_id=internal_id,
        model_alias="observe",
        description=f"Capture the current {environment} state explicitly for model inspection.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema=_OBSERVATION_SCHEMA,
        verification_policy=VerificationPolicy(execution_proof=ExecutionProof.STRUCTURED_RECEIPT),
        provenance=ToolProvenance(source=environment, reference=f"{environment}.observe"),
        version="1.9.0",
    )
    catalog.register(RegisteredTool(definition=definition, executor=_ObservationExecutor(service)))


def _register_adapted(
    catalog: ToolCatalog,
    spec: Any,
    *,
    internal_id: str,
    alias: str,
    environment: str,
    policy: VerificationPolicy,
    state_effects: tuple[str, ...],
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
    )
    catalog.register(
        RegisteredTool(
            definition=definition,
            executor=adapted.registered_tool.executor,
            verifier=_ReceiptVerifier()
            if policy.execution_proof is not ExecutionProof.NONE
            else None,
        )
    )


def _policy_for(name: str, *, environment: str) -> VerificationPolicy:
    if name in {"robot_manipulate", "robot_go_to"}:
        return VerificationPolicy(
            execution_proof=ExecutionProof.STRUCTURED_RECEIPT,
            requires_pre_observation="current_bound"
            if environment == "alfworld" or name == "robot_manipulate"
            else False,
            post_action_observation=PostActionObservation.FRESH_AFTER_BACKEND_ADVANCE,
        )
    if name in {"robot_verify"}:
        return VerificationPolicy(
            execution_proof=ExecutionProof.EXTERNAL_STATE,
            requires_pre_observation="current_bound" if environment == "home" else False,
        )
    if name == "browser_navigate":
        return VerificationPolicy(
            execution_proof=ExecutionProof.STRUCTURED_RECEIPT,
            post_action_observation=PostActionObservation.FRESH_AFTER_BACKEND_ADVANCE,
        )
    if name in {"browser_click", "browser_fill", "browser_select", "browser_wait"}:
        return VerificationPolicy(
            execution_proof=ExecutionProof.STRUCTURED_RECEIPT,
            requires_pre_observation="current_bound",
        )
    if name in {
        "task_planner",
        "task_progress_check",
        "skill_view",
        "memory_retriever",
        "memory_writer",
        "task_summarizer",
        "task_interpreter",
        "target_grounder",
    }:
        return VerificationPolicy(execution_proof=ExecutionProof.NONE)
    return VerificationPolicy(execution_proof=ExecutionProof.STRUCTURED_RECEIPT)


def _profile(catalog: ToolCatalog, environment: str, ids: list[str]) -> EnvironmentToolProfile:
    enabled = [internal_id for internal_id in ids if catalog.get(internal_id) is not None]
    return EnvironmentToolProfile(environment, catalog, catalog.freeze(enabled))


__all__ = [
    "EnvironmentToolProfile",
    "build_alfworld_profile",
    "build_coworker_profile",
    "build_environment_profiles",
    "build_home_profile",
]
