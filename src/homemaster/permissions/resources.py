"""Minimal device-adapter contract and the user-semantic action directory.

The permission core stays simulator-independent through this module: it
only sees exact resource keys and opaque binding references. Device
adapters (real hardware or simulator) own native handles, coordinates and
driver commands, and translate them here.

No simulator imports are allowed in this module; the interface audit
(`tests/homemaster/permissions/test_interface_audit.py`) enforces that.

Area semantics for adapters: an ``enter`` requirement is emitted only
when the call truly crosses into a different destination area, judged
from the current position against the target. Staying inside an area
emits nothing (an empty requirement list runs directly), returning to
an area after leaving needs a new request, and transit areas crossed on
the way never become requirements.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from importlib import resources as importlib_resources
from typing import TYPE_CHECKING, Any, Protocol

import yaml

if TYPE_CHECKING:
    from homemaster.permissions.models import (
        ExecutionObservation,
        PreparedPhysicalRequest,
    )
    from homemaster.tools.base import ToolExecutionContext, ToolResult

_BROAD_CALLS = ("use", "toggle", "manipulate")


class PhysicalDeviceAdapter(Protocol):
    """One backend behind the generic permission gate.

    prepare() is read-only and locks the binding; execute() runs exactly
    the prepared binding without re-selecting a target; observe() reads
    back the real outcome; release() drops the native binding on terminal
    or cancelled requests without deleting audit records.
    """

    async def prepare(
        self, call: Mapping[str, Any], context: ToolExecutionContext
    ) -> PreparedPhysicalRequest:
        """Resolve the exact resources of one call without side effects."""
        ...  # pragma: no cover

    async def execute(
        self, binding_ref: str, context: ToolExecutionContext
    ) -> ToolResult:
        """Execute one previously prepared binding."""
        ...  # pragma: no cover

    async def observe(
        self, binding_ref: str, context: ToolExecutionContext
    ) -> ExecutionObservation:
        """Read back the real outcome; never retry or move implicitly."""
        ...  # pragma: no cover

    async def release(self, request_id: str) -> None:
        """Release native bindings for one finished request."""
        ...  # pragma: no cover


@lru_cache(maxsize=1)
def _directory() -> dict[str, Any]:
    data_path = (
        importlib_resources.files(__package__).joinpath("actions.yaml")
    )
    try:
        raw = yaml.safe_load(data_path.read_bytes())
    except FileNotFoundError as exc:
        raise ValueError("action directory actions.yaml is missing") from exc
    if not isinstance(raw, dict):
        raise ValueError("action directory must be a mapping")
    if raw.get("version") != 1:
        raise ValueError("action directory version must be 1")
    actions = raw.get("actions")
    aliases = raw.get("aliases", {})
    if (
        not isinstance(actions, list)
        or not actions
        or any(not isinstance(item, str) or not item.strip() for item in actions)
    ):
        raise ValueError("action directory actions must be a non-empty string list")
    if len(set(actions)) != len(actions):
        raise ValueError("action directory actions must be unique")
    if not isinstance(aliases, dict) or any(
        not isinstance(source, str) or not isinstance(target, str)
        for source, target in aliases.items()
    ):
        raise ValueError("action directory aliases must map strings to strings")
    for source, target in aliases.items():
        if source in actions:
            raise ValueError(f"action alias {source!r} collides with a canonical action")
        if target not in actions:
            raise ValueError(f"action alias {source!r} points at unknown {target!r}")
    return {"actions": tuple(actions), "aliases": dict(aliases)}


def canonical_actions() -> tuple[str, ...]:
    """Return the canonical user-semantic action names."""
    return _directory()["actions"]


def action_aliases() -> dict[str, str]:
    """Return backend alias spellings mapped to canonical actions."""
    return dict(_directory()["aliases"])


def resolve_action(name: Any) -> str:
    """Map one spelling to its canonical action, deterministically.

    Broad calls (use/toggle/manipulate) are never mapped: the adapter must
    resolve them against the real device state first.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"unknown physical action {name!r}")
    normalized = name.strip().casefold()
    directory = _directory()
    if normalized in directory["actions"]:
        return normalized
    if normalized in directory["aliases"]:
        return directory["aliases"][normalized]
    if normalized in _BROAD_CALLS:
        raise ValueError(
            f"broad call {name!r} must be resolved by the device adapter "
            "to one concrete action first"
        )
    raise ValueError(
        f"unknown physical action {name!r}; "
        f"expected one of {sorted(directory['actions'])}"
    )


def is_canonical_action(name: Any) -> bool:
    """Return whether one spelling is already a canonical action."""
    return isinstance(name, str) and name.strip().casefold() in _directory()["actions"]


__all__ = [
    "PhysicalDeviceAdapter",
    "action_aliases",
    "canonical_actions",
    "is_canonical_action",
    "resolve_action",
]
