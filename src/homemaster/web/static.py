"""Serve the packaged HomeMaster Web Console production build."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

_DEFAULT_ROOT = Path(__file__).with_name("static_dist")


def mount_web_static(app: FastAPI, *, root: Path = _DEFAULT_ROOT) -> None:
    """Mount the SPA after API routes so it cannot shadow the control plane."""

    resolved = root.resolve()
    if not (resolved / "index.html").is_file():
        raise RuntimeError(
            "HomeMaster Web Console build is missing; run 'cd web && npm run build'"
        )
    app.mount("/", StaticFiles(directory=resolved, html=True), name="web-console")


__all__ = ["mount_web_static"]
