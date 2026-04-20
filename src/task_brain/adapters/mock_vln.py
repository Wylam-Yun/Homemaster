"""Mock VLN adapter for deterministic navigation simulation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from task_brain.world import MockWorld


class NavigationResult(BaseModel):
    """Normalized navigation adapter result."""

    status: str
    arrived: bool
    evidence: dict[str, Any]


class MockVLNAdapter:
    """Mock navigation adapter with strict viewpoint validation."""

    @staticmethod
    def navigate(world: MockWorld, viewpoint_id: str) -> NavigationResult:
        """Navigate to a viewpoint with evidence-carrying output.

        Invalid ``viewpoint_id`` is surfaced as ``ValueError``.
        Non-input errors are returned as failed navigation results.
        """
        try:
            room_id = world.get_viewpoint_room(viewpoint_id)
        except ValueError:
            raise
        except Exception as exc:  # pragma: no cover - defensive guardrail
            return NavigationResult(
                status="failed",
                arrived=False,
                evidence={
                    "source": "mock_vln",
                    "viewpoint_id": viewpoint_id,
                    "reason": "navigation_failed",
                    "error": str(exc),
                },
            )

        return NavigationResult(
            status="success",
            arrived=True,
            evidence={
                "source": "mock_vln",
                "viewpoint_id": viewpoint_id,
                "room_id": room_id,
            },
        )


__all__ = ["MockVLNAdapter", "NavigationResult"]
