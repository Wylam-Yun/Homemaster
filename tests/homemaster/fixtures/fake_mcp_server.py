"""Real FastMCP protocol fixture used by stdio and loopback HTTP tests.

Fixture shape adapted from OpenHarness 9b2efd7 MCP protocol tests.
"""

from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP

server = FastMCP("homemaster-test", json_response=True)


@server.tool()
def nested_query(
    mode: Literal["fast", "safe"],
    filters: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    """Return a structured response with the received nested arguments."""

    return {"mode": mode, "filters": filters or {}, "accepted": True}


@server.resource("fixture://readme")
def readme() -> str:
    """Return an independently observable resource payload."""

    return "homemaster-mcp-resource"


if __name__ == "__main__":
    server.run(transport="stdio")
