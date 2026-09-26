"""HomeMaster's single typed THOR Harness boundary."""

from __future__ import annotations

from typing import Any

from homemaster.alfworld.actions import OracleActionGateway
from homemaster.alfworld.backend import BackendReceipt, ThorBackend
from homemaster.alfworld.lifecycle import HarnessLifecycle
from homemaster.alfworld.outcomes import (
    AlfworldActionRequest,
    AlfworldExecutionFeedback,
    failure_feedback,
)
from homemaster.alfworld.recording import HarnessRecorder
from homemaster.alfworld.scene import SceneGroundingError


class AlfworldHarness:
    """Own reset, grounding, one gateway action, verification and close."""

    def __init__(self, backend: ThorBackend, *, recorder: HarnessRecorder | None = None) -> None:
        self.lifecycle = HarnessLifecycle(backend)
        self.gateway = OracleActionGateway(backend)
        self.recorder = recorder or HarnessRecorder()

    @property
    def closed(self) -> bool:
        return self.lifecycle.closed

    def reset(self, trial: Any) -> BackendReceipt:
        return self.lifecycle.reset(trial)

    def execute(self, request: AlfworldActionRequest) -> AlfworldExecutionFeedback:
        try:
            observed = self.lifecycle.observe()
            before = dict(observed.state)
            scene = self.lifecycle.capture_scene()
            snapshot_sequence = scene.sequence
            snapshot_generation = scene.generation
        except Exception:
            before = {}
            scene = None
            snapshot_sequence = None
            snapshot_generation = None
        action = str(request.arguments.get("action") or request.tool_name)
        try:
            if scene is None:
                raise RuntimeError("Harness has not been reset")
            current = self.lifecycle.observe()
            if (
                current.state_sequence != snapshot_sequence
                or current.scene_generation != snapshot_generation
            ):
                raise SceneGroundingError("scene snapshot is stale; action was not sent")
            target_label = str(
                request.arguments.get("target")
                or request.arguments.get("object")
                or request.arguments.get("target_receptacle")
                or ""
            ).strip()
            target = scene.ground(target_label) if target_label else None
            if request.tool_name == "robot_go_to":
                if target is None:
                    raise SceneGroundingError("robot_go_to requires target")
                receipt = self.gateway.go_to(target, scene)
            elif request.tool_name == "robot_manipulate":
                object_label = str(request.arguments.get("object") or "").strip()
                object_ref = scene.ground(object_label) if object_label else None
                target_label = str(request.arguments.get("target_receptacle") or "").strip()
                target_ref = scene.ground(target_label) if target_label else None
                receipt = self.gateway.manipulate(
                    action=str(request.arguments.get("action") or ""),
                    object_ref=object_ref,
                    target=target_ref,
                )
            elif request.tool_name == "robot_verify":
                observed = self.lifecycle.observe()
                receipt = BackendReceipt("verify", 0, True, observed.state)
            else:
                raise ValueError(f"unsupported Harness tool: {request.tool_name}")
        except SceneGroundingError as exc:
            after = self._safe_state()
            return failure_feedback(
                action=action,
                classification=(
                    "stale_scene_snapshot" if "stale" in str(exc) else "target_unresolved"
                ),
                receipt=BackendReceipt(action, 64, False, after, error=str(exc)),
                before=before,
                after=after,
            )
        except Exception as exc:
            after = self._safe_state()
            return failure_feedback(
                action=action,
                classification="harness_operation_failure",
                receipt=BackendReceipt(action, 1, True, after, error=str(exc)),
                before=before,
                after=after,
            )

        after = self._safe_state()
        success = (
            receipt.succeeded and after != before
            if request.tool_name != "robot_verify"
            else receipt.succeeded
        )
        feedback = AlfworldExecutionFeedback(
            action=action,
            success=success,
            classification=None if success else "external_state_unverified",
            external_return_code=receipt.external_return_code,
            backend_attempted=receipt.backend_attempted,
            terminal=bool(after.get("done")),
            won=bool(after.get("won")),
            evidence_refs=tuple(v for v in (receipt.evidence_ref,) if isinstance(v, str)),
            state_before=before,
            state_after=after,
        )
        self.recorder.record({"action": action, "feedback": feedback.__dict__})
        return feedback

    def advance_goal(self) -> None:
        self.lifecycle.advance_goal()

    def close(self) -> BackendReceipt:
        return self.lifecycle.close()

    def _safe_state(self) -> dict[str, Any]:
        try:
            return dict(self.lifecycle.observe().state)
        except Exception:
            return {}
