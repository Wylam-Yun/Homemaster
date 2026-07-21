"""Canonical model-facing profiles for Home, ALFWorld, and Coworker.

The benchmark modules continue to own their borrowed backends and scoring
logic.  This adapter owns only the stable Catalog/View projection and the
explicit ``observe`` capability shared by all three environments.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from homemaster.benchmarking.alfworld.registry import build_alfworld_tool_registry
from homemaster.benchmarking.coworker_demo.registry import build_coworker_tool_registry
from homemaster.domain.tool_registry import build_home_tool_registry
from homemaster.observations import ObservationCapture, ObservationService
from homemaster.tools.catalog import ToolCatalog, ToolView
from homemaster.tools.contracts import (
    ExecutionProof,
    ObservationReference,
    PostActionObservation,
    RegisteredTool,
    ResultImage,
    TerminalRule,
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


@dataclass
class HomeObservationBackend:
    """Structured Home state capture with explicit run and sequence identity."""

    run_id: str
    backend_id: str
    generation: int
    capture_state: Callable[[], Mapping[str, object]]
    state_sequence: int = 0
    event_sequence: int = 0

    def capture(self) -> ObservationCapture:
        self.event_sequence += 1
        return ObservationCapture(
            backend_id=self.backend_id,
            run_id=self.run_id,
            generation=self.generation,
            state_sequence=self.state_sequence,
            capture_event_sequence=self.event_sequence,
            media_type="application/json",
            content=dict(self.capture_state()),
            evidence_ref=f"home/{self.run_id}/observation/{self.event_sequence}",
        )


@dataclass
class AlfworldObservationBackend:
    """Raster capture adapter over the runner-owned ALFWorld environment."""

    adapter: Any
    run_id: str

    @property
    def backend_id(self) -> str:
        return str(self.adapter.backend_id)

    @property
    def generation(self) -> int:
        return int(self.adapter.generation)

    @property
    def state_sequence(self) -> int:
        return int(self.adapter.state_sequence)

    @property
    def event_sequence(self) -> int:
        return int(self.adapter.event_sequence)

    def capture(self) -> ObservationCapture:
        state = self.adapter.current_state
        if not state.frame_path:
            raise RuntimeError("ALFWorld raster observation has no current frame")
        path = Path(state.frame_path)
        return ObservationCapture(
            backend_id=self.backend_id,
            run_id=self.run_id,
            generation=self.generation,
            state_sequence=self.state_sequence,
            capture_event_sequence=self.event_sequence,
            media_type="image/png",
            content=path.read_bytes(),
            evidence_ref=f"alfworld/{state.episode_id}/frame/{self.event_sequence}",
        )


@dataclass
class CoworkerObservationBackend:
    """Structured DOM capture adapter over a runner-owned browser/client pair."""

    driver: Any
    client: Any
    run_id: str
    generation: int = 0
    capture_sequence: int = 0

    @property
    def backend_id(self) -> str:
        return f"coworker:{self.run_id}"

    @property
    def state_sequence(self) -> int:
        return int(self.client.state(self.run_id)["state_version"])

    @property
    def event_sequence(self) -> int:
        return self.capture_sequence

    def capture(self) -> ObservationCapture:
        self.capture_sequence += 1
        observation = self.driver.observe(f"observe-{self.capture_sequence:04d}")
        refs = observation.get("evidence_refs") or ()
        evidence_ref = str(refs[0]) if refs else f"coworker/{self.run_id}/dom"
        return ObservationCapture(
            backend_id=self.backend_id,
            run_id=self.run_id,
            generation=self.generation,
            state_sequence=self.state_sequence,
            capture_event_sequence=self.capture_sequence,
            media_type="application/json",
            content=observation,
            evidence_ref=evidence_ref,
        )


class _ObservationExecutor:
    def __init__(
        self,
        service: ObservationService,
        *,
        environment: str,
        raster: bool | None,
    ) -> None:
        self._service = service
        self._environment = environment
        self._raster = raster

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        del arguments
        record = await self._service.capture_for_model(context)
        if self._raster is not None and record.is_raster is not self._raster:
            ledger = getattr(context, "observation", None)
            if hasattr(ledger, "invalidate"):
                ledger.invalidate("observation media variant mismatch")
            return ToolExecutionResult(
                status=ToolExecutionStatus.FAILURE,
                error=ToolExecutionError(
                    "observation_media_mismatch",
                    f"{self._environment} observation has an unexpected media type",
                ),
                backend_attempted=False,
            )
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


class _HomeObservationExecutor(_ObservationExecutor):
    def __init__(self, service: ObservationService) -> None:
        super().__init__(service, environment="home", raster=False)


class _AlfworldObservationExecutor(_ObservationExecutor):
    def __init__(self, service: ObservationService) -> None:
        super().__init__(service, environment="alfworld", raster=True)


class _CoworkerObservationExecutor(_ObservationExecutor):
    def __init__(self, service: ObservationService) -> None:
        super().__init__(service, environment="coworker", raster=False)


class _ObservationVerifier:
    def __init__(self, *, environment: str, raster: bool) -> None:
        self._environment = environment
        self._raster = raster

    async def verify(
        self,
        result: ToolExecutionResult,
        context: ToolExecutionContext,
    ) -> VerificationRecord:
        del context
        refs = result.evidence_refs
        if result.status is not ToolExecutionStatus.SUCCESS:
            if refs:
                return VerificationRecord(
                    status=VerificationStatus.FAILED,
                    detail=f"{self._environment} observation capture failed",
                    evidence_refs=refs,
                )
            return VerificationRecord(
                status=VerificationStatus.PENDING,
                detail=f"{self._environment} observation has no failure evidence",
            )
        media_type = result.data.get("media_type")
        pixel_sha256 = result.data.get("pixel_sha256")
        shape_valid = (
            len(result.observations) == 1
            and bool(refs)
            and (isinstance(media_type, str) and media_type.startswith("image/"))
            is self._raster
            and (pixel_sha256 is not None) is self._raster
            and (len(result.images) == 1) is self._raster
        )
        if not shape_valid:
            if not refs:
                return VerificationRecord(
                    status=VerificationStatus.PENDING,
                    detail=f"{self._environment} observation lacks authoritative evidence",
                )
            return VerificationRecord(
                status=VerificationStatus.FAILED,
                detail=f"{self._environment} observation shape is invalid",
                evidence_refs=refs,
            )
        return VerificationRecord(
            status=VerificationStatus.PASSED,
            detail=f"{self._environment} observation record verified",
            evidence_refs=refs,
        )


class _HomeObservationVerifier(_ObservationVerifier):
    def __init__(self) -> None:
        super().__init__(environment="home", raster=False)


class _AlfworldObservationVerifier(_ObservationVerifier):
    def __init__(self) -> None:
        super().__init__(environment="alfworld", raster=True)


class _CoworkerObservationVerifier(_ObservationVerifier):
    def __init__(self) -> None:
        super().__init__(environment="coworker", raster=False)


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
    ordered: list[str] = [
        "observe",
        *(name for name in legacy.all_names() if name != "observe"),
    ]
    for name in legacy.all_names():
        if name == "observe":
            continue
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
        output_schema=_observation_schema(environment),
        verification_policy=VerificationPolicy(execution_proof=ExecutionProof.STRUCTURED_RECEIPT),
        provenance=ToolProvenance(source=environment, reference=f"{environment}.observe"),
        version="1.9.0",
    )
    executor_types = {
        "home": _HomeObservationExecutor,
        "alfworld": _AlfworldObservationExecutor,
        "coworker": _CoworkerObservationExecutor,
    }
    verifier_types = {
        "home": _HomeObservationVerifier,
        "alfworld": _AlfworldObservationVerifier,
        "coworker": _CoworkerObservationVerifier,
    }
    catalog.register(
        RegisteredTool(
            definition=definition,
            executor=executor_types[environment](service),
            verifier=verifier_types[environment](),
        )
    )


def _observation_schema(environment: str) -> dict[str, object]:
    properties = dict(_OBSERVATION_SCHEMA["properties"])  # type: ignore[arg-type]
    if environment == "alfworld":
        properties["media_type"] = {"type": "string", "pattern": "^image/"}
        properties["pixel_sha256"] = {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
        }
    else:
        properties["media_type"] = {
            "type": "string",
            "enum": ["application/json"],
        }
        properties["pixel_sha256"] = {"type": "null"}
    return {
        **_OBSERVATION_SCHEMA,
        "properties": properties,
        "title": f"{environment.title()}ObservationRecord",
    }


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
    "AlfworldObservationBackend",
    "CoworkerObservationBackend",
    "EnvironmentToolProfile",
    "HomeObservationBackend",
    "build_alfworld_profile",
    "build_coworker_profile",
    "build_environment_profiles",
    "build_home_profile",
]
