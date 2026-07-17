from __future__ import annotations

import hashlib

import pytest
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
    arguments: dict | None = None,
    result: dict | None = None,
) -> PresentationInput:
    return PresentationInput(
        runtime_event_type=event_type,
        tool_name=tool_name,
        status="succeeded",
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
        (EpisodePhase.VERIFYING, "add", "change_verified", 1, "operate_description"),
        (EpisodePhase.CREATED, "remove", "change_implement", 0, "operate_rollback"),
        (EpisodePhase.ROLLBACK_SUBMITTED, "add", "change_implement", 0, "operate_rollback"),
        (EpisodePhase.CREATED, "add", "change_implement", 0, "operate_description"),
    ],
)
def test_submit_and_wait_map_operations_to_exact_sop_source(
    store, tool_name: str, phase, operation: str, stage: str, index: int, field: str
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
            item("browser_wait", arguments={"operation": "remove"}),
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
