"""Single gateway for THOR navigation and manipulation actions."""

from __future__ import annotations

from typing import Any

from homemaster.alfworld.backend import BackendReceipt, ThorBackend
from homemaster.alfworld.scene import SceneObjectRef, SceneSnapshot


class OracleActionGateway:
    def __init__(self, backend: ThorBackend) -> None:
        self._backend = backend

    def go_to(self, target: SceneObjectRef, snapshot: SceneSnapshot) -> BackendReceipt:
        return self._backend.act(
            {
                "action": "TeleportFull",
                "objectId": target.object_id,
                "pose": snapshot.observation.get("agent_pose"),
            }
        )

    def manipulate(
        self,
        *,
        action: str,
        object_ref: SceneObjectRef | None,
        target: SceneObjectRef | None,
    ) -> BackendReceipt:
        payload: dict[str, Any] = {"action": action}
        if object_ref is not None:
            payload["objectId"] = object_ref.object_id
        if target:
            payload["target"] = target.object_id
        return self._backend.act(payload)
