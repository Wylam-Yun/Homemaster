"""ALFWorld device adapter for the V3.4 permission gate (Task 8).

Scope: ``robot_manipulate`` only. Standalone ``robot_go_to`` stays on the
legacy execution path: ALFWorld exposes no authoritative area model and
receptacle labels must not stand in for rooms, so area cases for this
backend are reported unsupported, never fabricated.

Mapping ownership: take/put spellings and every other simulation action
map inside this module; the generic permissions package never imports the
translator (guarded by ``tests/homemaster/permissions/test_interface_audit.py``
there and an AST check on this module in
``tests/homemaster/benchmarking/test_alfworld_permissions.py`` here).

Identity: ``environment_id`` comes from the backend episode id, so grants
never leak across scenes; object resource ids are backend objectIds pinned
through the authoritative scene index. A label that does not resolve to
exactly one backend object refuses preparation (fail closed) instead of
borrowing a same-named identity. Cross-reset id stability is NOT verified
on the available runtimes (no THOR on hkust4 at implementation time); the
acceptance report records real-backend verification as pending.

``prepare()`` is read-only and deterministic: the same call against the
same backend state yields identical requirement keys and binding refs, so
the executor's approval-wait revalidation detects target changes. No
randomness, no wall-clock inside hashed ids (timestamps ride along
outside the hashed payload).

Execution uses backend action spellings (take/put); permission keys use
canonical spellings (pick_up/place). The two are converted once, at
prepare time, inside this module.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from homemaster.permissions.models import (
    ExecutionObservation,
    PreparedPhysicalRequest,
    PreparedStep,
    Requirement,
    ResourceKey,
    TargetUnresolved,
)
from homemaster.permissions.resources import resolve_action
from homemaster.tools.base import ToolExecutionContext, ToolResult

SUPPORTED_TOOL = "robot_manipulate"

_ACTION_LABELS = {
    "pick_up": "拿取",
    "place": "放置",
    "open": "打开",
    "close": "关闭",
    "clean": "清洗",
    "heat": "加热",
    "cool": "冷却",
    "slice": "切",
    "turn_on": "打开",
    "turn_off": "关闭",
    "enter": "进入",
}

# Canonical -> backend spelling. Only aliased actions differ.
_BACKEND_ACTIONS = {"pick_up": "take", "place": "put"}

_HEAT_DEFAULT_RECEPTACLES = {"heat": "microwave", "cool": "fridge", "clean": "sinkbasin"}

# Mirrors the field roles in tools._ground_manipulate_arguments.
_GROUNDING_ROLES = {
    "object": frozenset({"object", "toggle"}),
    "source_receptacle": frozenset({"receptacle"}),
    "target_receptacle": frozenset({"receptacle", "toggle"}),
    "tool_receptacle": frozenset({"receptacle", "object"}),
}

_NAV_ROLES = frozenset({"object", "receptacle", "toggle"})


@dataclass(frozen=True)
class BackendTarget:
    """One backend identity pinned to exactly one native object."""

    object_id: str
    canonical_label: str
    kind: str
    display: str
    location: str


@dataclass(frozen=True)
class BackendReceipt:
    """Synchronous backend outcome. ``ok=None`` means unknown, never success."""

    ok: bool | None
    backend_code: str
    detail: str = ""


class AlfworldBackend(Protocol):
    """Narrow seam the adapter needs. Fakes implement this in tests."""

    def environment_id(self) -> str:
        """Stable episode/scene scope for grant isolation."""
        ...  # pragma: no cover

    def scene_revision(self) -> str:
        """Monotonic backend revision for change detection."""
        ...  # pragma: no cover

    def resolve_target(
        self, label: str, *, allowed: frozenset[str]
    ) -> BackendTarget | None:
        """Pin one label to exactly one native object, or None."""
        ...  # pragma: no cover

    def toggle_state(self, object_id: str) -> bool | None:
        """Current on/off for a toggle, or None when unreadable."""
        ...  # pragma: no cover

    async def go_to(self, canonical_label: str) -> BackendReceipt:
        """Move toward one locked label."""
        ...  # pragma: no cover

    async def manipulate(
        self, *, action: str, args: Mapping[str, Any]
    ) -> BackendReceipt:
        """Run one locked manipulation with already-resolved labels."""
        ...  # pragma: no cover


@dataclass
class _StepCall:
    kind: str  # "go_to" | "manipulate"
    label: str = ""
    action: str = ""
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class _StepOp:
    request_id: str
    binding_ref: str
    item_ids: tuple[str, ...]
    object_id: str | None
    call: _StepCall
    last_receipt: BackendReceipt | None = None


@dataclass(frozen=True)
class _PlannedEffect:
    canonical_action: str
    primary_label: str
    primary_allowed: frozenset[str]
    receptacle_label: str | None = None
    resolved_primary: BackendTarget | None = None


class AlfworldPermissionAdapter:
    """Permission-gate adapter for ALFWorld manipulation calls."""

    def __init__(
        self,
        *,
        tool: str = SUPPORTED_TOOL,
        backend: AlfworldBackend | None = None,
    ) -> None:
        if tool != SUPPORTED_TOOL:
            raise ValueError(
                f"unsupported tool {tool!r}; this adapter serves "
                f"{SUPPORTED_TOOL!r} only (standalone navigation stays legacy: "
                "no authoritative area model exists to gate it)"
            )
        self._tool = tool
        self._backend = backend
        self._ops: dict[str, _StepOp] = {}
        self._by_request: dict[str, set[str]] = {}

    @property
    def tool(self) -> str:
        return self._tool

    def _backend_for(self, context: ToolExecutionContext) -> AlfworldBackend:
        if self._backend is not None:
            return self._backend
        candidate = getattr(context, "backend", None)
        for attr in (
            "environment_id",
            "scene_revision",
            "resolve_target",
            "toggle_state",
            "go_to",
            "manipulate",
        ):
            if not callable(getattr(candidate, attr, None)):
                raise TargetUnresolved(
                    "no ALFWorld backend is wired for the permission adapter"
                )
        return candidate  # type: ignore[return-value]

    async def prepare(
        self, call: Mapping[str, Any], context: ToolExecutionContext
    ) -> PreparedPhysicalRequest:
        backend = self._backend_for(context)
        args = dict(call) if isinstance(call, Mapping) else {}
        env_id = backend.environment_id()
        scene_rev = backend.scene_revision()
        planned = self._plan(args, backend)
        primary = planned.resolved_primary
        if primary is None:
            primary = backend.resolve_target(
                planned.primary_label, allowed=planned.primary_allowed
            )
        if primary is None:
            raise TargetUnresolved(
                f"target unresolved: {planned.primary_label!r}"
            )
        nav_label = self._navigation_label(args, planned)
        nav_target: BackendTarget | None = None
        if nav_label is not None:
            nav_target = backend.resolve_target(nav_label, allowed=_NAV_ROLES)
            if nav_target is None:
                raise TargetUnresolved(
                    f"navigation target unresolved: {nav_label!r}"
                )
        recep_target: BackendTarget | None = None
        if planned.receptacle_label is not None:
            recep_target = backend.resolve_target(
                planned.receptacle_label, allowed=_NAV_ROLES
            )
            if recep_target is None:
                raise TargetUnresolved(
                    f"receptacle unresolved: {planned.receptacle_label!r}"
                )
        return self._build_request(
            args,
            planned,
            primary,
            nav_label,
            nav_target,
            recep_target,
            env_id,
            scene_rev,
            context,
        )

    def _plan(self, args: dict[str, Any], backend: AlfworldBackend) -> _PlannedEffect:
        raw_action = args.get("action")
        if not isinstance(raw_action, str) or not raw_action.strip():
            raise TargetUnresolved("manipulate call declares no action")
        spelling = raw_action.strip().lower()
        if spelling in {"toggle", "manipulate"}:
            raise TargetUnresolved(
                f"broad call {raw_action!r} carries no resolvable target semantics"
            )
        if spelling == "use":
            return self._plan_use(args, backend)
        try:
            canonical = resolve_action(spelling)
        except ValueError as exc:
            raise TargetUnresolved(str(exc)) from exc
        if canonical == "pick_up":
            return _PlannedEffect(
                canonical_action=canonical,
                primary_label=_required_text(args.get("object"), "take object"),
                primary_allowed=_GROUNDING_ROLES["object"],
            )
        if canonical in {"open", "close"}:
            label = args.get("target_receptacle") or args.get("object")
            return _PlannedEffect(
                canonical_action=canonical,
                primary_label=_required_text(label, f"{canonical} target"),
                primary_allowed=_GROUNDING_ROLES["target_receptacle"]
                | _GROUNDING_ROLES["object"],
            )
        if canonical == "place":
            recep = args.get("target_receptacle") or args.get("object")
            return _PlannedEffect(
                canonical_action=canonical,
                primary_label=_required_text(args.get("object"), "place object"),
                primary_allowed=_GROUNDING_ROLES["object"],
                receptacle_label=_required_text(recep, "place receptacle"),
            )
        if canonical in {"heat", "cool", "clean"}:
            recep = (
                args.get("tool_receptacle")
                or args.get("target_receptacle")
                or _HEAT_DEFAULT_RECEPTACLES[canonical]
            )
            return _PlannedEffect(
                canonical_action=canonical,
                primary_label=_required_text(args.get("object"), f"{canonical} object"),
                primary_allowed=_GROUNDING_ROLES["object"],
                receptacle_label=_required_text(recep, f"{canonical} receptacle"),
            )
        if canonical == "slice":
            # The held knife is a tool precondition, not a separate state
            # change; only the sliced object is declared.
            return _PlannedEffect(
                canonical_action=canonical,
                primary_label=_required_text(args.get("object"), "slice object"),
                primary_allowed=_GROUNDING_ROLES["object"],
            )
        if canonical in {"turn_on", "turn_off"}:
            return _PlannedEffect(
                canonical_action=canonical,
                primary_label=_required_text(args.get("object"), f"{canonical} target"),
                primary_allowed=_GROUNDING_ROLES["object"]
                | _GROUNDING_ROLES["target_receptacle"],
            )
        raise TargetUnresolved(f"action {spelling!r} has no ALFWorld mapping")

    def _plan_use(
        self, args: dict[str, Any], backend: AlfworldBackend
    ) -> _PlannedEffect:
        label = _required_text(args.get("object"), "use target")
        target = backend.resolve_target(label, allowed=frozenset({"toggle"}))
        if target is None or target.kind != "toggle":
            raise TargetUnresolved(
                f"use target unresolved as a toggle: {label!r}"
            )
        state = backend.toggle_state(target.object_id)
        if state is None:
            raise TargetUnresolved(
                f"toggle state unreadable for {label!r}; refusing blind use"
            )
        return _PlannedEffect(
            canonical_action="turn_off" if state else "turn_on",
            primary_label=label,
            primary_allowed=frozenset({"toggle"}),
            resolved_primary=target,
        )

    def _navigation_label(
        self, args: dict[str, Any], planned: _PlannedEffect
    ) -> str | None:
        from homemaster.benchmarking.alfworld.tools import (
            navigation_target_for_action,
        )

        probe = dict(args)
        probe["action"] = planned.canonical_action
        return navigation_target_for_action(probe)

    def _build_request(
        self,
        args: dict[str, Any],
        planned: _PlannedEffect,
        primary: BackendTarget,
        nav_label: str | None,
        nav_target: BackendTarget | None,
        recep_target: BackendTarget | None,
        env_id: str,
        scene_rev: str,
        context: ToolExecutionContext,
    ) -> PreparedPhysicalRequest:
        del recep_target  # identity locked inside bindings, not a separate item
        action_label = _ACTION_LABELS[planned.canonical_action]
        call_hash = _call_hash(
            {
                "tool": self._tool,
                "env": env_id,
                "scene": scene_rev,
                "action": planned.canonical_action,
                "primary": primary.object_id,
                "nav": nav_target.object_id if nav_target is not None else None,
            }
        )
        request_id = f"alfworld-{call_hash[:16]}"
        item_id = _stable_id(
            "item", env_id, "object", primary.object_id, planned.canonical_action
        )
        step_ids: list[str] = []
        if nav_label is not None:
            step_ids.append(f"step-nav-{call_hash[:12]}")
        step_ids.append(f"step-op-{call_hash[:12]}")
        requirement = Requirement(
            item_id=item_id,
            key=ResourceKey(
                environment_id=env_id,
                resource_kind="object",
                resource_id=primary.object_id,
                action=planned.canonical_action,
            ),
            display_name=primary.display,
            location=primary.location,
            action_label=action_label,
            step_ids=tuple(step_ids),
        )
        steps: list[PreparedStep] = []
        locked_args = _locked_args(args, planned, primary, nav_label)
        if nav_label is not None:
            binding_ref = f"bind-{step_ids[0]}"
            steps.append(
                PreparedStep(
                    step_id=step_ids[0],
                    binding_ref=binding_ref,
                    required_item_ids=(item_id,),
                    summary=f"navigate to {nav_label}",
                )
            )
            self._register_op(
                request_id,
                binding_ref,
                (item_id,),
                nav_target.object_id if nav_target is not None else primary.object_id,
                _StepCall(kind="go_to", label=nav_label),
            )
        binding_ref = f"bind-{step_ids[-1]}"
        steps.append(
            PreparedStep(
                step_id=step_ids[-1],
                binding_ref=binding_ref,
                required_item_ids=(item_id,),
                summary=f"{planned.canonical_action} {primary.canonical_label}",
            )
        )
        self._register_op(
            request_id,
            binding_ref,
            (item_id,),
            primary.object_id,
            _StepCall(
                kind="manipulate",
                action=_BACKEND_ACTIONS.get(
                    planned.canonical_action, planned.canonical_action
                ),
                args=locked_args,
            ),
        )
        created = datetime.now(timezone.utc)
        metadata = getattr(context, "metadata", None)
        if not isinstance(metadata, Mapping):
            metadata = {}
        session_id = metadata.get("session_id") or f"session-{call_hash[:12]}"
        run_id = metadata.get("run_id") or f"run-{call_hash[:12]}"
        return PreparedPhysicalRequest(
            request_id=request_id,
            approval_id=f"{request_id}-approval",
            environment_id=env_id,
            session_id=str(session_id),
            run_id=str(run_id),
            intent_id=f"intent-{call_hash[:12]}",
            intent_summary=f"{action_label}{primary.display}（{primary.location}）",
            revision=1,
            requirements=(requirement,),
            steps=tuple(steps),
            target_snapshot_revision=scene_rev,
            created_at=_iso(created),
            deadline_at=_iso(created + timedelta(seconds=300)),
        )

    def _register_op(
        self,
        request_id: str,
        binding_ref: str,
        item_ids: tuple[str, ...],
        object_id: str | None,
        call: _StepCall,
    ) -> None:
        self._ops[binding_ref] = _StepOp(
            request_id=request_id,
            binding_ref=binding_ref,
            item_ids=item_ids,
            object_id=object_id,
            call=call,
        )
        self._by_request.setdefault(request_id, set()).add(binding_ref)

    async def execute(
        self, binding_ref: str, context: ToolExecutionContext
    ) -> ToolResult:
        backend = self._backend_for(context)
        op = self._ops.get(binding_ref)
        if op is None:
            raise TargetUnresolved(f"unknown binding {binding_ref!r}")
        if op.call.kind == "go_to":
            receipt = await backend.go_to(op.call.label)
        else:
            receipt = await backend.manipulate(
                action=op.call.action, args=dict(op.call.args)
            )
        op.last_receipt = receipt
        return _result_for_receipt(op, receipt)

    async def observe(
        self, binding_ref: str, context: ToolExecutionContext
    ) -> ExecutionObservation:
        del context
        op = self._ops.get(binding_ref)
        if op is None:
            return ExecutionObservation(
                outcome="unknown",
                backend_code="unknown binding",
                binding_ref=binding_ref,
                observed_resource_id=None,
                current_area_id=None,
                evidence_ref="unobserved",
            )
        receipt = op.last_receipt
        if receipt is None:
            return ExecutionObservation(
                outcome="unknown",
                backend_code="unobserved",
                binding_ref=binding_ref,
                observed_resource_id=op.object_id,
                current_area_id=None,
                evidence_ref="unobserved",
            )
        return ExecutionObservation(
            outcome=_outcome_for(receipt.ok),
            backend_code=receipt.backend_code,
            binding_ref=binding_ref,
            observed_resource_id=op.object_id,
            current_area_id=None,
            evidence_ref=f"{op.request_id}:{binding_ref}:{receipt.backend_code}",
        )

    async def release(self, request_id: str) -> None:
        for binding_ref in self._by_request.pop(request_id, ()):
            self._ops.pop(binding_ref, None)


class ThorBackendView:
    """Production AlfworldBackend over a live AlfworldEnvAdapter.

    Real-backend verification is PENDING (no THOR runtime on hkust4 when
    written): every method is a thin seam translation. Text-world (TWEnv)
    execution needs translator-built commands, which the permission path
    must not re-derive, so only AlfredThorEnv is served; anything else
    resolves to None and the adapter fails closed. Toggle state has no
    public seam, so ``use`` stays refused until one exists.
    """

    def __init__(
        self,
        env: Any,
        *,
        subtask: Any = None,
        judge_config_path: Any = None,
    ) -> None:
        self._env = env
        self._subtask = subtask
        self._judge_config_path = judge_config_path

    def environment_id(self) -> str:
        return str(self._env.backend_id)

    def scene_revision(self) -> str:
        return f"{self._env.generation}:{self._env.state_sequence}"

    def resolve_target(
        self, label: str, *, allowed: frozenset[str]
    ) -> BackendTarget | None:
        from homemaster.benchmarking.alfworld.grounding import (
            resolve_authoritative,
        )

        index = self._env.authoritative_object_index
        if index is None:
            return None
        try:
            state = self._env.current_state
        except RuntimeError:
            return None
        target = resolve_authoritative(
            label,
            state=state,
            subtask=self._subtask,
            allowed_kinds=set(allowed),
            judge_config_path=self._judge_config_path,
            scene_index=index,
        )
        if target is None:
            return None
        kind = target.kind if target.kind in allowed else None
        if kind is None:
            return None
        reference = target.reference
        return BackendTarget(
            object_id=str(reference.object_id),
            canonical_label=str(reference.canonical_label),
            kind=kind,
            display=str(reference.canonical_label),
            location=str(self.environment_id()),
        )

    def toggle_state(self, object_id: str) -> bool | None:
        del object_id
        return None

    async def go_to(self, canonical_label: str) -> BackendReceipt:
        result = self._env.go_to_target(
            canonical_label,
            tool_name="robot_manipulate",
            tool_args={"action": "go_to", "target": canonical_label},
        )
        return _receipt_from_step(result)

    async def manipulate(
        self, *, action: str, args: Mapping[str, Any]
    ) -> BackendReceipt:
        if str(getattr(self._env, "env_type", "AlfredThorEnv")) != "AlfredThorEnv":
            return BackendReceipt(
                ok=None,
                backend_code="unsupported-env-type",
                detail="text-world commands need translator-built strings",
            )
        result = self._env.manipulate_with_thor(
            action=action,
            tool_name="robot_manipulate",
            tool_args=dict(args),
        )
        return _receipt_from_step(result)


def _receipt_from_step(step_result: Any) -> BackendReceipt:
    ok = getattr(step_result, "success", None)
    failure = getattr(step_result, "failure_reason", None)
    feedback = getattr(step_result, "feedback", "")
    return BackendReceipt(
        ok=bool(ok) if isinstance(ok, bool) else None,
        backend_code=str(failure) if failure else "thor-ok",
        detail=str(feedback or ""),
    )


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TargetUnresolved(f"manipulate call declares no {label}")
    return value.strip()


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()
    return f"{parts[0]}-{digest[:12]}"


def _call_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _locked_args(
    args: dict[str, Any],
    planned: _PlannedEffect,
    primary: BackendTarget,
    nav_label: str | None,
) -> dict[str, Any]:
    locked: dict[str, Any] = {
        "action": _BACKEND_ACTIONS.get(
            planned.canonical_action, planned.canonical_action
        )
    }
    if isinstance(args.get("object"), str) and args["object"].strip():
        locked["object"] = primary.canonical_label
    for fld in ("source_receptacle", "target_receptacle", "tool_receptacle"):
        value = args.get(fld)
        if isinstance(value, str) and value.strip():
            locked[fld] = value.strip()
    if nav_label is not None:
        locked["_navigation_target"] = nav_label
    return locked


def _outcome_for(ok: bool | None) -> str:
    if ok is True:
        return "succeeded"
    if ok is False:
        return "no_effect_failure"
    return "unknown"


def _result_for_receipt(op: _StepOp, receipt: BackendReceipt) -> ToolResult:
    metadata = {
        "status": "success"
        if receipt.ok is True
        else ("outcome_unknown" if receipt.ok is None else "execution_failed"),
        "backend_attempted": True,
        "backend_code": receipt.backend_code,
        "binding_ref": op.binding_ref,
    }
    if receipt.detail:
        metadata["backend_detail"] = receipt.detail
    if receipt.ok is True:
        return ToolResult(
            f"alfworld {op.call.kind} completed ({receipt.backend_code})",
            False,
            metadata,
        )
    if receipt.ok is None:
        return ToolResult(
            f"alfworld {op.call.kind} outcome is unknown ({receipt.backend_code})",
            True,
            metadata,
        )
    return ToolResult(
        f"alfworld {op.call.kind} failed ({receipt.backend_code})",
        True,
        metadata,
    )


__all__ = [
    "AlfworldBackend",
    "AlfworldPermissionAdapter",
    "BackendReceipt",
    "BackendTarget",
    "SUPPORTED_TOOL",
    "ThorBackendView",
]
