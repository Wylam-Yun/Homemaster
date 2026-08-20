"""Typed JSON envelopes exposed to the browser console."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class WebEvent:
    """One browser-facing event after Runtime field projection."""

    type: str
    session_id: str
    run_id: str
    request_id: str
    payload: Mapping[str, object]


__all__ = ["WebEvent"]
