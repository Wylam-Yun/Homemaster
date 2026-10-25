"""Per-domain HTTP route registration for the Web Console.

``create_web_app`` builds the app and one shared ``WebAppContext``, then
each module below mounts one path domain — the same ``register(app, ...)``
shape ``mount_sse_stream``/``mount_web_static`` already use. Registration
order here follows the sequence the routes were declared in the old
monolithic factory; no two domains share a path template, so ordering is
not load-bearing.
"""

from __future__ import annotations

from fastapi import FastAPI

from homemaster.web.context import WebAppContext
from homemaster.web.routes import (
    approvals,
    memory,
    permissions,
    providers,
    questions,
    runs,
    session_ops,
    sessions,
    skills,
    system,
)


def register_routes(app: FastAPI, ctx: WebAppContext) -> None:
    """Mount every API domain on ``app``."""

    sessions.register(app, ctx)
    memory.register(app, ctx)
    runs.register(app, ctx)
    providers.register(app, ctx)
    system.register(app, ctx)
    skills.register(app, ctx)
    session_ops.register(app, ctx)
    approvals.register(app, ctx)
    questions.register(app, ctx)
    permissions.register(app, ctx)


__all__ = ["register_routes"]
