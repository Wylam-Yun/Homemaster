from __future__ import annotations

from importlib.resources import files


def test_installed_package_contains_web_resources() -> None:
    package_root = files("case02_openenv")
    for relative in (
        "static/app.css",
        "static/automation.js",
        "static/monitor.js",
        "static/observer.js",
        "static/ticket.js",
        "templates/automation.html",
        "templates/monitor.html",
        "templates/observer.html",
        "templates/ticket.html",
    ):
        assert package_root.joinpath(*relative.split("/")).is_file(), relative
