from __future__ import annotations

from dataclasses import dataclass

from homemaster.alfworld import AlfworldActionRequest, AlfworldHarness
from homemaster.alfworld.backend import BackendReceipt
from homemaster.alfworld.scene import SceneSnapshot


@dataclass
class FakeBackend:
    state: dict
    sequence: int = 0
    closed: bool = False

    def reset(self, trial):
        self.state = {"objects": [{"name": "mug 1", "objectId": "Mug|1"}], "trial": trial}
        self.sequence += 1
        return _receipt("reset", self.state)

    def observe(self):
        from homemaster.alfworld.backend import ThorObservation

        return ThorObservation(dict(self.state), 1, self.sequence)

    def act(self, action):
        self.sequence += 1
        self.state = {**self.state, "last_action": action, "changed": self.sequence}
        return _receipt("act", self.state)

    def close(self):
        self.closed = True
        return _receipt("close", {"closed": True})


@dataclass
class FailureBackend(FakeBackend):
    action_code: int = 1
    mutate_on_failure: bool = False
    close_error: str | None = None

    def act(self, action):
        self.sequence += 1
        if self.mutate_on_failure:
            self.state = {**self.state, "unexpected": True}
        return BackendReceipt(
            "act",
            self.action_code,
            True,
            dict(self.state),
            evidence_ref="act:failure",
            error="backend rejected action",
        )

    def close(self):
        if self.close_error:
            raise RuntimeError(self.close_error)
        return super().close()


def _receipt(operation, state):
    from homemaster.alfworld.backend import BackendReceipt

    return BackendReceipt(operation, 0, True, dict(state), evidence_ref=f"{operation}:1")


def test_harness_requires_reset_and_verifies_external_state_change():
    backend = FakeBackend({})
    harness = AlfworldHarness(backend)
    before_reset = harness.execute(
        AlfworldActionRequest("robot_go_to", {"target": "mug 1"})
    )
    assert before_reset.classification == "harness_operation_failure"
    assert harness.reset("trial-1").external_return_code == 0
    result = harness.execute(AlfworldActionRequest("robot_go_to", {"target": "mug 1"}))
    assert result.success is True
    assert result.external_return_code == 0
    assert result.backend_attempted is True
    assert result.evidence_refs == ("act:1",)
    assert harness.close().external_return_code == 0
    assert backend.closed is True


def test_harness_grounds_worker_object_type_when_thor_name_has_runtime_suffix():
    backend = FakeBackend({})
    def reset_with_runtime_name(_trial):
        backend.state = {
            "objects": [
                {
                    "name": "Mug_a12c171b",
                    "objectType": "Mug",
                    "objectId": "Mug|-02.05|+01.35|+00.45",
                }
            ]
        }
        backend.sequence += 1
        return _receipt("reset", backend.state)

    backend.reset = reset_with_runtime_name  # type: ignore[method-assign]
    harness = AlfworldHarness(backend)
    harness.reset("trial-1")

    result = harness.execute(AlfworldActionRequest("robot_go_to", {"target": "mug"}))

    assert result.success is True
    assert result.external_return_code == 0
    assert backend.state["last_action"]["objectId"] == "Mug|-02.05|+01.35|+00.45"


def test_scene_snapshot_uses_stable_type_instance_labels():
    snapshot = SceneSnapshot(
        {
            "objects": [
                {
                    "name": "RemoteControl_z",
                    "objectType": "RemoteControl",
                    "objectId": "RemoteControl|z",
                },
                {
                    "name": "RemoteControl_a",
                    "objectType": "RemoteControl",
                    "objectId": "RemoteControl|a",
                },
            ]
        },
        generation=1,
        sequence=1,
    )

    assert snapshot.ground("remotecontrol").object_id == "RemoteControl|a"
    assert snapshot.ground("remotecontrol 2").object_id == "RemoteControl|z"


def test_harness_rejects_ambiguous_or_missing_grounding_without_backend_action():
    backend = FakeBackend({})
    harness = AlfworldHarness(backend)
    harness.reset("trial-1")
    result = harness.execute(AlfworldActionRequest("robot_go_to", {"target": "missing"}))
    assert result.success is False
    assert result.classification == "target_unresolved"
    assert result.backend_attempted is False
    assert "last_action" not in backend.state


def test_harness_preserves_backend_failure_and_does_not_claim_state_change():
    backend = FailureBackend({})
    harness = AlfworldHarness(backend)
    harness.reset("trial-1")

    result = harness.execute(AlfworldActionRequest("robot_go_to", {"target": "mug 1"}))

    assert result.success is False
    assert result.external_return_code == 1
    assert result.backend_attempted is True
    assert result.classification == "external_state_unverified"
    assert result.state_before == result.state_after


def test_harness_rejects_stale_scene_before_sending_action():
    backend = FakeBackend({})
    harness = AlfworldHarness(backend)
    harness.reset("trial-1")

    original_observe = backend.observe
    calls = 0

    def stale_observe():
        nonlocal calls
        calls += 1
        if calls == 3:
            backend.sequence += 1
        return original_observe()

    backend.observe = stale_observe  # type: ignore[method-assign]
    result = harness.execute(AlfworldActionRequest("robot_go_to", {"target": "mug 1"}))

    assert result.success is False
    assert result.classification == "stale_scene_snapshot"
    assert "last_action" not in backend.state


def test_harness_closes_once_and_surfaces_close_failure():
    backend = FailureBackend({}, close_error="cleanup failed")
    harness = AlfworldHarness(backend)
    harness.reset("trial-1")

    try:
        harness.close()
    except RuntimeError as exc:
        assert str(exc) == "cleanup failed"
    else:
        raise AssertionError("close failure was swallowed")
    assert harness.closed is False
