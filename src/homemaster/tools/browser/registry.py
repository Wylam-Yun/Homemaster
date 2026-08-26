"""Central registry for the V3.1 model-facing browser tool modules."""

from homemaster.tools.adapters import from_registered_tool
from homemaster.tools.base import ToolRegistry
from homemaster.tools.browser import (
    analyze,
    backfill,
    check,
    click,
    console,
    dialog,
    download,
    drag,
    eval,
    extract,
    fill,
    find,
    focus,
    history,
    hover,
    inspect,
    navigate,
    network,
    press,
    read,
    screenshot,
    scroll,
    select,
    tabs,
    type_tool,
    uncheck,
    upload,
    wait,
)

_BUILDERS = (
    navigate.build,
    history.build,
    inspect.build,
    find.build,
    read.build,
    extract.build,
    screenshot.build,
    console.build,
    analyze.build,
    click.build,
    fill.build,
    type_tool.build,
    select.build,
    check.build,
    uncheck.build,
    hover.build,
    focus.build,
    press.build,
    scroll.build,
    upload.build,
    drag.build,
    backfill.build,
    tabs.build,
    dialog.build,
    network.build,
    download.build,
    wait.build,
)


def build_browser_registered_tools(session: object):
    tools = [builder(session) for builder in _BUILDERS]
    if bool(getattr(getattr(session, "policy", None), "eval_allowed", False)):
        tools.append(eval.build(session))
    return tuple(tools)


def build_browser_run_registry(base: ToolRegistry, session: object) -> ToolRegistry:
    if not isinstance(base, ToolRegistry):
        raise TypeError("base must be a ToolRegistry")
    browser_names = {
        "browser_navigate",
        "browser_history",
        "browser_inspect",
        "browser_find",
        "browser_read",
        "browser_extract",
        "browser_screenshot",
        "browser_console",
        "browser_analyze",
        "browser_click",
        "browser_fill",
        "browser_type",
        "browser_select",
        "browser_check",
        "browser_uncheck",
        "browser_hover",
        "browser_focus",
        "browser_press",
        "browser_scroll",
        "browser_upload",
        "browser_drag",
        "browser_backfill",
        "browser_tabs",
        "browser_dialog",
        "browser_network",
        "browser_download",
        "browser_wait",
        "browser_eval",
        "observe",
    }
    registry = ToolRegistry()
    registry.register_many(
        tuple(tool for tool in base.list_tools() if tool.name not in browser_names)
    )
    registry.register_many(
        tuple(from_registered_tool(tool) for tool in build_browser_registered_tools(session))
    )
    return registry.freeze()


__all__ = ["build_browser_registered_tools", "build_browser_run_registry"]
