"""Harness lifecycle ownership and reset/close invariants."""

from __future__ import annotations

from typing import Any

from homemaster.alfworld.backend import BackendReceipt, ThorBackend, ThorObservation
from homemaster.alfworld.scene import SceneSnapshot


class HarnessLifecycle:
    def __init__(self, backend: ThorBackend) -> None:
        self.backend = backend
        self.scene: SceneSnapshot | None = None
        self.closed = False
        self._goal_generation = 0

    def reset(self, trial: Any) -> BackendReceipt:
        if self.closed:
            raise RuntimeError("cannot reset a closed Harness")
        receipt = self.backend.reset(trial)
        if receipt.succeeded:
            observed = self.backend.observe()
            self.scene = SceneSnapshot(
                observed.state,
                generation=observed.scene_generation,
                sequence=observed.state_sequence,
            )
            self._goal_generation += 1
        return receipt

    def observe(self) -> ThorObservation:
        if self.closed:
            raise RuntimeError("Harness is closed")
        if self.scene is None:
            raise RuntimeError("Harness has not been reset")
        return self.backend.observe()

    def capture_scene(self) -> SceneSnapshot:
        observed = self.observe()
        self.scene = SceneSnapshot(
            observed.state,
            generation=observed.scene_generation,
            sequence=observed.state_sequence,
        )
        return self.scene

    def advance_goal(self) -> None:
        if self.closed:
            raise RuntimeError("Harness is closed")
        self._goal_generation += 1

    def close(self) -> BackendReceipt:
        if self.closed:
            return BackendReceipt("close", 0, False, {"already_closed": True})
        receipt = self.backend.close()
        self.closed = True
        return receipt
