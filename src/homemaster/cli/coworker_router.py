"""Deterministic utterance-to-ticket routing with no no-match side effects."""

from __future__ import annotations

import re
from pathlib import Path

from homemaster.benchmarking.coworker_demo.ticket_bundle import (
    BundleValidationError,
    CaseRepository,
)
from homemaster.benchmarking.coworker_demo.types import (
    InvalidTicketRoute,
    NoTicketRoute,
    TicketRoute,
    ValidTicketRoute,
)

_ABSOLUTE_JSON = re.compile(r"(?<![\w.-])(/[^^\s'\"<>]+?\.json)(?=$|[\s'\"<>，。；;])")
_SCENARIO_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(normal|post_change_anomaly)(?![A-Za-z0-9_])")


def route_coworker_ticket(text: str) -> TicketRoute:
    candidates = [Path(match) for match in _ABSOLUTE_JSON.findall(text)]
    if not candidates:
        if ".json" in text.casefold():
            return InvalidTicketRoute(
                error_code="ticket_path_missing",
                message="ticket intent requires one absolute .json path",
            )
        return NoTicketRoute()
    unique = list(dict.fromkeys(candidates))
    if len(unique) != 1:
        return InvalidTicketRoute(
            error_code="multiple_ticket_paths",
            message="ticket intent must contain exactly one JSON path",
        )
    tokens = set(_SCENARIO_TOKEN.findall(text))
    if len(tokens) > 1:
        return InvalidTicketRoute(
            error_code="ambiguous_scenario",
            message="choose exactly one stable scenario token",
        )
    scenario_id = next(iter(tokens), "normal")
    ticket_path = unique[0]
    if not ticket_path.is_file():
        return InvalidTicketRoute(
            error_code="ticket_not_found",
            message=f"ticket file does not exist: {ticket_path}",
        )
    case_root = ticket_path.parent.parent
    try:
        bundle = CaseRepository(case_root).resolve(ticket_path, scenario_id)
    except BundleValidationError as exc:
        return InvalidTicketRoute(error_code="invalid_ticket_bundle", message=str(exc))
    return ValidTicketRoute(
        ticket_path=bundle.ticket_path,
        case_root=bundle.case_root,
        scenario_id=scenario_id,
        locked_hashes=dict(bundle.locked_hashes),
    )
