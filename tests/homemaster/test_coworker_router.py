from __future__ import annotations

from pathlib import Path

from homemaster.benchmarking.coworker_demo.types import TicketRouteKind
from homemaster.cli.coworker_router import route_coworker_ticket

TICKET = Path("data/coworker_demo/case_02/test_set/item_change_ticket.json").resolve()


def test_plain_messages_are_side_effect_free_no_match() -> None:
    assert route_coworker_ticket("请帮我检查客厅里的杯子").kind == TicketRouteKind.NO_MATCH
    assert route_coworker_ticket("explain JSON parsing").kind == TicketRouteKind.NO_MATCH


def test_valid_path_defaults_to_normal_and_locks_hashes() -> None:
    route = route_coworker_ticket(f"请执行这个变更单 '{TICKET}'")
    assert route.kind == TicketRouteKind.VALID_TICKET
    assert route.scenario_id == "normal"
    assert route.ticket_path == TICKET
    assert set(route.locked_hashes) == {"manifest", "ticket", "scenario", "trajectory_dag"}


def test_exact_anomaly_token_selects_scenario() -> None:
    route = route_coworker_ticket(f"用 post_change_anomaly 场景执行 {TICKET}")
    assert route.kind == TicketRouteKind.VALID_TICKET
    assert route.scenario_id == "post_change_anomaly"


def test_missing_multiple_and_ambiguous_intents_are_invalid(tmp_path: Path) -> None:
    assert route_coworker_ticket("执行 missing.json").kind == TicketRouteKind.INVALID_TICKET_INTENT
    second = tmp_path / "second.json"
    second.write_text("{}", encoding="utf-8")
    multiple = route_coworker_ticket(f"执行 {TICKET} 和 {second}")
    assert multiple.error_code == "multiple_ticket_paths"
    ambiguous = route_coworker_ticket(f"normal post_change_anomaly {TICKET}")
    assert ambiguous.error_code == "ambiguous_scenario"
