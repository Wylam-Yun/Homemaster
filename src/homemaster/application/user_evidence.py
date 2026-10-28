"""Run-context user evidence registration (V3.8).

Moved out of the removed automatic-recall service: registering the current
user statement into the memory evidence ledger is unrelated to automatic
Search — it stays as independent run wiring so downstream evidence refs
(memory feedback, audit) keep resolving to a real turn id.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def register_user_evidence(
    *,
    settings: Any,
    request: Any,
    session_id: str,
    run_id: str,
    turn_index: int,
) -> tuple[str, ...]:
    """Register the current user statement in the evidence ledger, if any."""
    services = getattr(settings, "application_services", {})
    ledger = services.get("memory_evidence_ledger") if isinstance(services, Mapping) else None
    register = getattr(ledger, "register", None)
    if not callable(register):
        return ()
    evidence = register(
        kind="user_statement",
        tenant_id=request.permission_subject.tenant_id,
        session_id=session_id,
        run_id=run_id,
        turn_id=f"turn-{turn_index}",
    )
    return (evidence.ref,)


__all__ = ["register_user_evidence"]
