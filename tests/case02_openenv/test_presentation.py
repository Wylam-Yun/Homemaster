from __future__ import annotations

import hashlib
import json

import pytest
from case02_openenv.episode_store import EpisodeError
from case02_openenv.models import EpisodePhase
from case02_openenv.presentation import (
    CONFIG_BIDS,
    DISPLAY_STAGES,
    MONITOR_BIDS,
    ORCHESTRATION_TOOLS,
    PresentationEvent,
    PresentationInput,
    PresentationMappingError,
    display_stage,
    map_task,
    ticket_task,
)
from pydantic import ValidationError


def item(
    tool_name: str | None = None,
    *,
    event_type: str = "tool.call_completed",
    status: str | None = None,
    arguments: dict | None = None,
    result: dict | None = None,
) -> PresentationInput:
    if status is None:
        status = {
            "tool.call_started": "running",
            "tool.call_completed": "succeeded",
            "tool.call_failed": "failed",
            "model.public_reply": "succeeded",
            "runtime.turn_completed": "succeeded",
            "runtime.turn_failed": "failed",
        }[event_type]
    return PresentationInput(
        runtime_event_type=event_type,
        tool_call_id="mapping-call" if event_type.startswith("tool.") else None,
        action_id="mapping-action" if event_type.startswith("tool.") else None,
        tool_name=tool_name,
        status=status,
        arguments=arguments or {},
        result=result or {},
    )


def assert_ticket_source(task, ticket: dict, stage: str, index: int, field: str) -> None:
    source = ticket[stage][index]
    assert task.stage == stage
    assert task.check_name == source["check_name"]
    assert task.source_field == field
    assert task.source_text == source[field]
    assert task.source_sha256 == hashlib.sha256(source[field].encode("utf-8")).hexdigest()


def test_store_persists_public_reply_and_reclassifies_it_on_runtime_completion(store) -> None:
    run_id = "presentation-public-reply"
    store.create(run_id, "normal")
    store.record_presentation(
        run_id,
        PresentationInput(
            runtime_event_type="model.public_reply",
            status="succeeded",
            public_model_output={
                "kind": "assistant_reply",
                "text": "I will inspect the current state.",
                "outcome": "intermediate",
            },
        ),
    )
    assert store.presentation_snapshot(run_id)["public_model_output"]["outcome"] == ("intermediate")

    store.record_presentation(run_id, item(event_type="runtime.turn_completed"))
    assert store.presentation_snapshot(run_id)["public_model_output"]["outcome"] == ("premature")


def test_store_maps_monitor_to_exact_pre_and_post_sop_text(store) -> None:
    run_id = "presentation-map"
    store.create(run_id, "normal")
    episode = store.episode(run_id)
    call = item("browser_click", arguments={"bid": "monitor-query-alarm"})

    before = store.presentation_task(run_id, call)
    assert_ticket_source(before, episode.ticket, "check_before_change", 0, "operate_description")
    assert store.presentation_stage(run_id, call, before) == "check_before_change"

    episode.state.phase = EpisodePhase.CHANGE_APPLIED
    after = store.presentation_task(run_id, call)
    assert_ticket_source(after, episode.ticket, "change_verified", 0, "operate_description")
    assert after.source_text != before.source_text
    assert store.presentation_stage(run_id, call, after) == "change_verified"


def test_config_and_business_verification_use_exact_ticket_steps(store) -> None:
    run_id = "presentation-config-business"
    store.create(run_id, "normal")
    episode = store.episode(run_id)

    config = store.presentation_task(
        run_id,
        item("browser_click", arguments={"bid": "ticket-query-extension-config"}),
    )
    assert_ticket_source(config, episode.ticket, "check_before_change", 1, "operate_description")

    episode.state.phase = EpisodePhase.VERIFYING
    business_call = item(
        "browser_click",
        arguments={"bid": "automation-submit", "operation": "business_verify"},
    )
    business = store.presentation_task(run_id, business_call)
    assert_ticket_source(business, episode.ticket, "change_verified", 1, "operate_description")
    assert store.presentation_stage(run_id, business_call, business) == "business_verify"


def test_unknown_control_fails_closed_without_mutating_current_task(store) -> None:
    run_id = "presentation-fail-closed"
    store.create(run_id, "normal")
    previous = store.presentation_task(
        run_id,
        item("browser_click", arguments={"bid": "monitor-query-probe"}),
    )
    with pytest.raises(PresentationMappingError, match="trusted SOP mapping"):
        store.presentation_task(
            run_id,
            item("browser_click", arguments={"bid": "business-control-not-locked"}),
        )
    assert store.episode(run_id).current_presentation_task is previous


def test_unknown_navigation_route_fails_closed_without_mutating_current_task(store) -> None:
    run_id = "presentation-unknown-route"
    store.create(run_id, "normal")
    previous = store.presentation_task(
        run_id,
        item("browser_click", arguments={"bid": "monitor-query-probe"}),
    )
    with pytest.raises(PresentationMappingError, match="trusted SOP mapping"):
        store.presentation_task(
            run_id,
            item("browser_navigate", arguments={"route": "not-a-trusted-route"}),
        )
    assert store.episode(run_id).current_presentation_task is previous


@pytest.mark.parametrize("bid", sorted(CONFIG_BIDS))
def test_all_config_bids_map_to_locked_config_step(store, bid: str) -> None:
    store.create(f"config-{bid}", "normal")
    episode = store.episode(f"config-{bid}")
    task = map_task(
        episode.ticket, episode.state, item("browser_click", arguments={"bid": bid}), None
    )
    assert_ticket_source(task, episode.ticket, "check_before_change", 1, "operate_description")


@pytest.mark.parametrize("bid", sorted(MONITOR_BIDS))
@pytest.mark.parametrize(
    ("phase", "stage"),
    [(EpisodePhase.CREATED, "check_before_change"), (EpisodePhase.COMPLETED, "change_verified")],
)
def test_all_monitor_bids_use_phase_specific_locked_step(
    store, bid: str, phase, stage: str
) -> None:
    store.create(f"monitor-{phase.value}-{bid}", "normal")
    episode = store.episode(f"monitor-{phase.value}-{bid}")
    episode.state.phase = phase
    task = map_task(
        episode.ticket, episode.state, item("browser_click", arguments={"bid": bid}), None
    )
    assert_ticket_source(task, episode.ticket, stage, 0, "operate_description")


@pytest.mark.parametrize(
    ("phase", "arguments", "expected_stage", "field"),
    [
        (EpisodePhase.CREATED, {"route": "ticket"}, "check_before_change", "operate_description"),
        (EpisodePhase.CREATED, {"route": "monitor"}, "check_before_change", "operate_description"),
        (
            EpisodePhase.CHANGE_APPLIED,
            {"route": "monitor"},
            "change_verified",
            "operate_description",
        ),
        (
            EpisodePhase.ROLLBACK_SUBMITTED,
            {"route": "automation"},
            "change_implement",
            "operate_rollback",
        ),
        (EpisodePhase.CREATED, {"route": "automation"}, "change_implement", "operate_description"),
    ],
)
def test_browser_navigation_routes_to_exact_sop_source(
    store, phase, arguments: dict, expected_stage: str, field: str
) -> None:
    store.create("nav-map", "normal")
    episode = store.episode("nav-map")
    episode.state.phase = phase
    task = map_task(
        episode.ticket, episode.state, item("browser_navigate", arguments=arguments), None
    )
    index = 0
    assert_ticket_source(task, episode.ticket, expected_stage, index, field)


def test_automation_navigation_retains_previous_task(store) -> None:
    store.create("nav-previous", "normal")
    episode = store.episode("nav-previous")
    previous = ticket_task(episode.ticket, "change_verified", 1, "operate_description")
    call = item("browser_navigate", arguments={"route": "automation"})
    assert map_task(episode.ticket, episode.state, call, previous) is previous


def test_automation_navigation_replaces_unrelated_previous_task_with_add_sop(store) -> None:
    store.create("nav-unrelated", "normal")
    episode = store.episode("nav-unrelated")
    previous = ticket_task(episode.ticket, "check_before_change", 1, "operate_description")

    task = map_task(
        episode.ticket,
        episode.state,
        item("browser_navigate", arguments={"route": "automation"}),
        previous,
    )

    assert task is not previous
    assert_ticket_source(task, episode.ticket, "change_implement", 0, "operate_description")


def test_automation_navigation_retains_implementation_task(store) -> None:
    store.create("nav-implementation", "normal")
    episode = store.episode("nav-implementation")
    previous = ticket_task(episode.ticket, "change_implement", 0, "operate_verified")
    call = item("browser_navigate", arguments={"route": "automation"})
    assert map_task(episode.ticket, episode.state, call, previous) is previous


@pytest.mark.parametrize("tool_name", ["browser_fill", "browser_select"])
@pytest.mark.parametrize(
    ("phase", "value", "stage", "index", "field"),
    [
        (EpisodePhase.CREATED, "remove", "change_implement", 0, "operate_rollback"),
        (EpisodePhase.ROLLBACK_SUBMITTED, "add", "change_implement", 0, "operate_rollback"),
        (EpisodePhase.CREATED, "business_verify", "change_verified", 1, "operate_description"),
        (
            EpisodePhase.CREATED,
            "svc_usage_record_fetcher",
            "change_verified",
            1,
            "operate_description",
        ),
        (EpisodePhase.CREATED, "add", "change_implement", 0, "operate_description"),
    ],
)
def test_fill_and_select_map_values_to_exact_sop_source(
    store, tool_name: str, phase, value: str, stage: str, index: int, field: str
) -> None:
    store.create(f"{tool_name}-{phase.value}-{value}", "normal")
    episode = store.episode(f"{tool_name}-{phase.value}-{value}")
    episode.state.phase = phase
    task = map_task(
        episode.ticket,
        episode.state,
        item(tool_name, arguments={"bid": "automation-operation", "value": value}),
        None,
    )
    assert_ticket_source(task, episode.ticket, stage, index, field)


@pytest.mark.parametrize("tool_name", ["browser_click", "browser_wait"])
@pytest.mark.parametrize(
    ("phase", "operation", "stage", "index", "field"),
    [
        (EpisodePhase.CREATED, "business_verify", "change_verified", 1, "operate_description"),
        (EpisodePhase.VERIFYING, "add", "change_implement", 0, "operate_description"),
        (EpisodePhase.VERIFYING, "remove", "change_implement", 0, "operate_rollback"),
        (EpisodePhase.VERIFYING, None, "change_verified", 1, "operate_description"),
        (EpisodePhase.CREATED, "remove", "change_implement", 0, "operate_rollback"),
        (EpisodePhase.ROLLBACK_SUBMITTED, "add", "change_implement", 0, "operate_rollback"),
        (
            EpisodePhase.ROLLBACK_SUBMITTED,
            "business_verify",
            "change_implement",
            0,
            "operate_rollback",
        ),
        (EpisodePhase.CREATED, "add", "change_implement", 0, "operate_description"),
    ],
)
def test_submit_and_wait_map_operations_to_exact_sop_source(
    store, tool_name: str, phase, operation: str | None, stage: str, index: int, field: str
) -> None:
    store.create(f"{tool_name}-{phase.value}-{operation}", "normal")
    episode = store.episode(f"{tool_name}-{phase.value}-{operation}")
    episode.state.phase = phase
    bid = "automation-submit" if tool_name == "browser_click" else "automation-job"
    task = map_task(
        episode.ticket,
        episode.state,
        item(tool_name, arguments={"bid": bid, "operation": operation}),
        None,
    )
    assert_ticket_source(task, episode.ticket, stage, index, field)


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [("browser_click", {"bid": "automation-submit"}), ("browser_wait", {})],
)
def test_submit_or_wait_without_operation_retains_previous_business_task(
    store, tool_name: str, arguments: dict
) -> None:
    store.create(f"no-operation-previous-{tool_name}", "normal")
    episode = store.episode(f"no-operation-previous-{tool_name}")
    previous = ticket_task(episode.ticket, "change_verified", 1, "operate_description")
    call = item(tool_name, arguments=arguments)
    assert map_task(episode.ticket, episode.state, call, previous) is previous


def test_submit_without_operation_or_previous_maps_add_sop(store) -> None:
    store.create("submit-empty-add", "normal")
    episode = store.episode("submit-empty-add")
    call = item("browser_click", arguments={"bid": "automation-submit"})
    task = map_task(episode.ticket, episode.state, call, None)
    assert_ticket_source(task, episode.ticket, "change_implement", 0, "operate_description")


def test_wait_without_operation_retains_previous_implementation_task(store) -> None:
    store.create("wait-empty-implementation", "normal")
    episode = store.episode("wait-empty-implementation")
    previous = ticket_task(
        episode.ticket,
        "change_implement",
        0,
        "operate_description",
    )

    assert map_task(episode.ticket, episode.state, item("browser_wait"), previous) is previous


def test_wait_without_operation_or_compatible_previous_fails_closed(store) -> None:
    store.create("wait-empty-unrelated", "normal")
    episode = store.episode("wait-empty-unrelated")
    previous = ticket_task(episode.ticket, "check_before_change", 0, "operate_description")
    with pytest.raises(PresentationMappingError, match="trusted SOP mapping"):
        map_task(episode.ticket, episode.state, item("browser_wait"), previous)


@pytest.mark.parametrize("tool_name", ["browser_click", "browser_wait"])
def test_unknown_nonempty_automation_operation_fails_closed_and_retains_current_task(
    store, tool_name: str
) -> None:
    run_id = f"unknown-operation-{tool_name}"
    store.create(run_id, "normal")
    previous = store.presentation_task(
        run_id,
        item("browser_click", arguments={"bid": "monitor-query-alarm"}),
    )
    arguments = {"operation": "not-a-trusted-operation"}
    if tool_name == "browser_click":
        arguments["bid"] = "automation-submit"
    with pytest.raises(PresentationMappingError, match="trusted SOP mapping"):
        store.presentation_task(run_id, item(tool_name, arguments=arguments))
    assert store.episode(run_id).current_presentation_task is previous


def test_operation_can_be_read_from_result(store) -> None:
    store.create("result-operation", "normal")
    episode = store.episode("result-operation")
    call = item("browser_wait", result={"operation": "remove"})
    task = map_task(episode.ticket, episode.state, call, None)
    assert_ticket_source(task, episode.ticket, "change_implement", 0, "operate_rollback")


@pytest.mark.parametrize(
    ("phase", "field"),
    [
        (EpisodePhase.CREATED, "operate_verified"),
        (EpisodePhase.ROLLBACK_SUBMITTED, "operate_rollback"),
    ],
)
def test_terminal_execution_maps_implementation_verification_or_rollback(
    store, phase, field: str
) -> None:
    store.create(f"terminal-{phase.value}", "normal")
    episode = store.episode(f"terminal-{phase.value}")
    episode.state.phase = phase
    task = map_task(episode.ticket, episode.state, item("terminal_execute"), None)
    assert_ticket_source(task, episode.ticket, "change_implement", 0, field)


@pytest.mark.parametrize("tool_name", sorted(ORCHESTRATION_TOOLS) + ["browser_observe"])
def test_orchestration_and_observation_retain_previous_task(store, tool_name: str) -> None:
    store.create(f"retain-{tool_name}", "normal")
    episode = store.episode(f"retain-{tool_name}")
    previous = ticket_task(episode.ticket, "check_before_change", 0, "operate_description")
    assert map_task(episode.ticket, episode.state, item(tool_name), previous) is previous


@pytest.mark.parametrize("event_type", ["runtime.turn_completed", "runtime.turn_failed"])
def test_runtime_turn_events_retain_previous_task(store, event_type: str) -> None:
    store.create(f"turn-{event_type}", "normal")
    episode = store.episode(f"turn-{event_type}")
    previous = ticket_task(episode.ticket, "check_before_change", 0, "operate_description")
    assert (
        map_task(episode.ticket, episode.state, item(event_type=event_type), previous) is previous
    )


def test_ticket_task_rejects_empty_locked_source(store) -> None:
    store.create("empty-source", "normal")
    ticket = store.episode("empty-source").ticket.copy()
    ticket["change_implement"] = [ticket["change_implement"][0].copy()]
    ticket["change_implement"][0]["operate_verified"] = ""
    with pytest.raises(PresentationMappingError, match="trusted SOP mapping"):
        ticket_task(ticket, "change_implement", 0, "operate_verified")


@pytest.mark.parametrize("field", ["check_name", "source_text", "source_sha256"])
def test_returned_presentation_task_cannot_be_mutated(store, field: str) -> None:
    store.create(f"frozen-task-{field}", "normal")
    task = store.presentation_task(
        f"frozen-task-{field}",
        item("browser_click", arguments={"bid": "monitor-query-alarm"}),
    )
    with pytest.raises(ValidationError):
        setattr(task, field, "caller-corruption")


@pytest.mark.parametrize(
    ("phase", "call", "task_stage", "task_index", "expected"),
    [
        (
            EpisodePhase.CREATED,
            item("browser_click", arguments={"bid": "monitor-query-alarm"}),
            "check_before_change",
            0,
            "check_before_change",
        ),
        (
            EpisodePhase.CHANGE_SUBMITTED,
            item("browser_navigate", arguments={"route": "automation"}),
            "change_implement",
            0,
            "change_implement",
        ),
        (
            EpisodePhase.CHANGE_APPLIED,
            item("browser_click", arguments={"bid": "monitor-query-alarm"}),
            "change_verified",
            0,
            "change_verified",
        ),
        (
            EpisodePhase.CHANGE_APPLIED,
            item("terminal_execute"),
            "change_implement",
            0,
            "implementation_verify",
        ),
        (
            EpisodePhase.CHANGE_APPLIED,
            item("browser_wait", arguments={"operation": "add"}),
            "change_implement",
            0,
            "implementation_verify",
        ),
        (
            EpisodePhase.CHANGE_APPLIED,
            item("browser_wait", arguments={"operation": "business_verify"}),
            "change_verified",
            1,
            "business_verify",
        ),
        (
            EpisodePhase.ROLLBACK_SUBMITTED,
            item("browser_wait", arguments={"operation": "business_verify"}),
            "change_implement",
            0,
            "change_rollback",
        ),
    ],
)
def test_display_stage_is_specific_to_the_live_action(
    store, phase, call, task_stage: str, task_index: int, expected: str
) -> None:
    store.create(f"display-{expected}", "normal")
    episode = store.episode(f"display-{expected}")
    episode.state.phase = phase
    field = (
        "operate_rollback" if phase == EpisodePhase.ROLLBACK_SUBMITTED else "operate_description"
    )
    task = ticket_task(episode.ticket, task_stage, task_index, field)
    assert display_stage(episode.ticket, episode.state, call, task) == expected


@pytest.mark.parametrize("event_type", ["runtime.turn_completed", "runtime.turn_failed"])
def test_display_stage_is_terminal_for_turn_events(store, event_type: str) -> None:
    store.create(f"display-{event_type}", "normal")
    episode = store.episode(f"display-{event_type}")
    assert (
        display_stage(episode.ticket, episode.state, item(event_type=event_type), None)
        == "terminal"
    )


def test_display_stage_is_terminal_for_outcome(store) -> None:
    store.create("display-outcome", "normal")
    episode = store.episode("display-outcome")
    episode.state.terminal_outcome = "completed"
    assert display_stage(episode.ticket, episode.state, item("browser_observe"), None) == "terminal"


def test_public_constants_and_input_factories_are_stable() -> None:
    assert DISPLAY_STAGES == {
        "check_before_change",
        "change_implement",
        "implementation_verify",
        "change_verified",
        "business_verify",
        "change_rollback",
        "terminal",
    }
    first = item("browser_observe")
    second = item("browser_observe")
    first.arguments["x"] = 1
    first.result["x"] = 1
    first.evidence_refs.append("ev")
    assert second.arguments == {}
    assert second.result == {}
    assert second.evidence_refs == []
    assert first.timestamp.tzinfo is not None


def test_presentation_event_requires_identity_and_timestamp() -> None:
    with pytest.raises(ValidationError):
        PresentationEvent(
            sequence=1,
            run_id="event-contract",
            event_type="tool.call_completed",
            stage="check_before_change",
            status="succeeded",
        )


def presentation_item(
    *,
    event_type: str = "tool.call_started",
    status: str = "running",
    call_id: str | None = "call-1",
    bid: str = "ticket-query-extension-config",
) -> PresentationInput:
    return PresentationInput(
        runtime_event_type=event_type,
        tool_call_id=call_id,
        action_id="action-1",
        tool_name="browser_click",
        status=status,
        arguments={"bid": bid},
        result={"message": "ready"} if status == "succeeded" else {},
    )


def test_store_appends_presentation_events_and_persists_snapshot(store) -> None:
    run_id = "presentation-ledger"
    store.create(run_id, "normal")

    first = store.record_presentation(run_id, presentation_item())
    second = store.record_presentation(
        run_id,
        presentation_item(event_type="tool.call_completed", status="succeeded"),
    )

    assert [first.sequence, second.sequence] == [1, 2]
    assert first.event_id.startswith("presentation-00001-")
    assert second.event_id.startswith("presentation-00002-")
    assert first.stage == "check_before_change"
    assert second.task.source_field == "operate_description"
    snapshot = store.presentation_snapshot(run_id)
    assert snapshot["schema_version"] == 2
    assert snapshot["stage"] == second.stage
    assert snapshot["last_event"]["event_id"] == second.event_id
    assert snapshot["last_sequence"] == 2
    assert snapshot["in_flight"] == []
    assert len(snapshot["completed_steps"]) == 1
    assert snapshot["next_step"] == second.task.check_name
    root = store.episode(run_id).run_root / "presentation"
    lines = (root / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["sequence"] for line in lines] == [1, 2]
    assert json.loads((root / "snapshot.json").read_text(encoding="utf-8")) == snapshot


def test_terminal_event_reuses_correlated_start_task_when_controls_are_omitted(
    store,
) -> None:
    run_id = "presentation-correlated-task"
    store.create(run_id, "normal")
    started = store.record_presentation(run_id, presentation_item())

    completed = store.record_presentation(
        run_id,
        PresentationInput(
            runtime_event_type="tool.call_completed",
            tool_call_id="call-1",
            action_id="action-1",
            tool_name="browser_click",
            status="succeeded",
            result={"check": "extension_config", "ready": True},
        ),
    )

    assert completed.task == started.task
    assert completed.failure is None
    assert store.presentation_snapshot(run_id)["presentation_failures"] == []


def test_mapping_failure_is_recorded_without_replacing_trusted_task(store) -> None:
    run_id = "presentation-mapping-failure"
    store.create(run_id, "normal")
    trusted = store.record_presentation(run_id, presentation_item())

    failed = store.record_presentation(
        run_id,
        presentation_item(
            bid="not-trusted",
            event_type="tool.call_completed",
            status="succeeded",
        ),
    )

    assert failed.failure is not None
    assert failed.task == trusted.task
    snapshot = store.presentation_snapshot(run_id)
    assert snapshot["current_task"] == trusted.task.model_dump(mode="json")
    assert snapshot["presentation_failures"] == [failed.failure]
    assert snapshot["completed_steps"] == []


def test_presentation_snapshot_tracks_calls_dedupes_steps_and_returns_copies(store) -> None:
    run_id = "presentation-snapshot"
    store.create(run_id, "normal")
    running = store.record_presentation(run_id, presentation_item(call_id="call-1"))
    legacy_no_id = presentation_item().model_copy(update={"tool_call_id": None})
    store.record_presentation(run_id, legacy_no_id)
    store.record_presentation(
        run_id,
        presentation_item(call_id="call-1", event_type="tool.call_completed", status="succeeded"),
    )
    store.record_presentation(
        run_id,
        presentation_item(call_id="call-2", event_type="tool.call_completed", status="succeeded"),
    )

    snapshot = store.presentation_snapshot(run_id)
    assert snapshot["in_flight"] == []
    assert len(snapshot["completed_steps"]) == 1
    returned = store.presentation_events(run_id)
    returned[0].arguments["caller"] = "corruption"
    assert "caller" not in store.presentation_events(run_id)[0].arguments
    assert running.tool_call_id == "call-1"


def test_reset_clears_presentation_state_and_artifacts(store) -> None:
    run_id = "presentation-reset"
    store.create(run_id, "normal")
    store.record_presentation(run_id, presentation_item())
    root = store.episode(run_id).run_root / "presentation"
    assert (root / "events.jsonl").is_file()
    assert (root / "snapshot.json").is_file()

    store.reset(run_id)

    assert store.presentation_events(run_id) == []
    assert store.presentation_snapshot(run_id)["last_sequence"] == 0
    assert not (root / "events.jsonl").exists()
    assert not (root / "snapshot.json").exists()
    assert store.presentation_snapshot(run_id)["next_step"] == "等待 Agent 读取变更单"


@pytest.mark.parametrize("call_id", [None, ""])
def test_running_events_without_a_tool_call_id_do_not_occupy_in_flight(
    store, call_id: str | None
) -> None:
    run_id = f"presentation-no-call-{call_id!s}"
    store.create(run_id, "normal")
    legacy_item = presentation_item().model_copy(update={"tool_call_id": call_id})
    store.record_presentation(run_id, legacy_item)

    assert store.presentation_snapshot(run_id)["in_flight"] == []


def test_terminal_outcome_overrides_the_last_presentation_event_stage(store) -> None:
    run_id = "presentation-terminal-stage"
    store.create(run_id, "normal")
    event = store.record_presentation(run_id, presentation_item())
    assert event.stage == "check_before_change"

    store.episode(run_id).state.terminal_outcome = "completed"

    assert store.presentation_snapshot(run_id)["stage"] == "terminal"


def test_rollback_phase_overrides_the_last_presentation_event_stage(store) -> None:
    run_id = "presentation-rollback-stage"
    store.create(run_id, "normal")
    event = store.record_presentation(run_id, presentation_item())
    assert event.stage == "check_before_change"

    store.episode(run_id).state.phase = EpisodePhase.ROLLBACK_SUBMITTED

    assert store.presentation_snapshot(run_id)["stage"] == "change_rollback"


@pytest.mark.parametrize("bid", ["ticket-query-extension-config", "not-trusted"])
def test_append_failure_leaves_presentation_memory_and_files_unchanged(
    store, monkeypatch, bid: str
) -> None:
    run_id = f"presentation-append-failure-{bid}"
    store.create(run_id, "normal")
    episode = store.episode(run_id)
    events_path = episode.run_root / "presentation/events.jsonl"
    snapshot_path = episode.run_root / "presentation/snapshot.json"

    def fail_append(path, *_args, **_kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"partial-event")
        raise OSError("append failed")

    monkeypatch.setattr("case02_openenv.episode_store.append_jsonl", fail_append)
    with pytest.raises(OSError, match="append failed"):
        store.record_presentation(run_id, presentation_item(bid=bid))

    assert episode.current_presentation_task is None
    assert episode.presentation_failures == []
    assert episode.presentation_events == []
    assert not events_path.exists()
    assert not snapshot_path.exists()


def test_jsonl_rollback_failure_raises_explicit_consistency_error(store, monkeypatch) -> None:
    run_id = "presentation-rollback-failure"
    store.create(run_id, "normal")

    def fail_append(*_args, **_kwargs):
        raise OSError("append failed")

    def fail_rollback(*_args, **_kwargs):
        raise OSError("rollback failed")

    monkeypatch.setattr("case02_openenv.episode_store.append_jsonl", fail_append)
    monkeypatch.setattr(store, "_rollback_presentation_jsonl", fail_rollback)

    with pytest.raises(EpisodeError) as caught:
        store.record_presentation(run_id, presentation_item())
    assert caught.value.code == "presentation_consistency_error"
    assert caught.value.status_code == 500


def test_snapshot_failure_rolls_back_jsonl_and_memory(store, monkeypatch) -> None:
    run_id = "presentation-snapshot-failure"
    store.create(run_id, "normal")
    store.record_presentation(run_id, presentation_item())
    episode = store.episode(run_id)
    events_path = episode.run_root / "presentation/events.jsonl"
    snapshot_path = episode.run_root / "presentation/snapshot.json"
    before_events = [event.model_dump(mode="json") for event in episode.presentation_events]
    before_task = episode.current_presentation_task
    before_failures = list(episode.presentation_failures)
    before_jsonl = events_path.read_bytes()
    before_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    def fail_snapshot(*_args, **_kwargs):
        raise OSError("snapshot failed")

    monkeypatch.setattr("case02_openenv.episode_store.atomic_write_json", fail_snapshot)
    with pytest.raises(OSError, match="snapshot failed"):
        store.record_presentation(
            run_id,
            presentation_item(
                bid="monitor-query-alarm",
                event_type="tool.call_completed",
                status="succeeded",
            ),
        )

    assert [event.model_dump(mode="json") for event in episode.presentation_events] == before_events
    assert episode.current_presentation_task is before_task
    assert episode.presentation_failures == before_failures
    assert events_path.read_bytes() == before_jsonl
    assert json.loads(snapshot_path.read_text(encoding="utf-8")) == before_snapshot


def test_reset_advances_atomic_presentation_stream_generation(store) -> None:
    run_id = "presentation-stream-generation"
    store.create(run_id, "normal")
    first = store.record_presentation(run_id, presentation_item())
    generation, events, snapshot = store.presentation_stream_state(run_id)
    assert events == [first]
    assert snapshot["last_sequence"] == 1

    store.reset(run_id)

    reset_generation, reset_events, reset_snapshot = store.presentation_stream_state(run_id)
    assert reset_generation == generation + 1
    assert reset_events == []
    assert reset_snapshot["last_sequence"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {
            "runtime_event_type": "tool.call_completed",
            "status": "running",
            "tool_call_id": "call-1",
            "action_id": "action-1",
        },
        {
            "runtime_event_type": "tool.call_started",
            "status": "running",
            "action_id": "action-1",
        },
        {
            "runtime_event_type": "tool.call_started",
            "status": "running",
            "tool_call_id": "call-1",
            "action_id": "",
        },
    ],
)
def test_presentation_input_rejects_incoherent_tool_lifecycle(payload: dict) -> None:
    with pytest.raises(ValidationError):
        PresentationInput(**payload)


@pytest.mark.parametrize(
    ("event_type", "status"),
    [("runtime.turn_completed", "succeeded"), ("runtime.turn_failed", "failed")],
)
def test_runtime_turn_lifecycle_does_not_require_tool_identity(
    event_type: str, status: str
) -> None:
    parsed = PresentationInput(runtime_event_type=event_type, status=status)
    assert parsed.tool_call_id is None
    assert parsed.action_id is None


def test_presentation_verifier_requires_terminal_results_for_every_tool(store) -> None:
    run_id = "presentation-verify"
    store.create(run_id, "normal")
    store.record_presentation(
        run_id,
        PresentationInput(
            runtime_event_type="tool.call_started",
            tool_call_id="call-1",
            action_id="action-1",
            tool_name="browser_click",
            status="running",
            arguments={"bid": "ticket-query-extension-config"},
        ),
    )

    report = store.verify_presentation(run_id, observer_was_alive=True)

    assert report["passed"] is False
    assert report["failures"] == ["missing_terminal_event:call-1"]

    store.record_presentation(
        run_id,
        PresentationInput(
            runtime_event_type="tool.call_completed",
            tool_call_id="call-1",
            action_id="action-1",
            tool_name="browser_click",
            status="succeeded",
            arguments={"bid": "ticket-query-extension-config"},
            result={"status": "ready"},
        ),
    )

    report = store.verify_presentation(run_id, observer_was_alive=True)

    assert report["passed"] is True
    assert report["event_count"] == 2
    assert report["tool_call_count"] == 1
    assert (
        json.loads(
            (store.episode(run_id).run_root / "presentation/verification.json").read_text(
                encoding="utf-8"
            )
        )
        == report
    )


def test_presentation_verifier_rejects_action_mismatch_and_dead_observer(store) -> None:
    run_id = "presentation-health"
    store.create(run_id, "normal")
    store.record_presentation(run_id, presentation_item())
    completed = presentation_item(
        event_type="tool.call_completed",
        status="succeeded",
    ).model_copy(update={"action_id": "different-action"})
    store.record_presentation(run_id, completed)

    report = store.verify_presentation(run_id, observer_was_alive=False)

    assert report["passed"] is False
    assert report["observer_was_alive"] is False
    assert report["failures"] == [
        "action_id_mismatch:call-1",
        "observer_exited_before_recording_stop",
    ]
