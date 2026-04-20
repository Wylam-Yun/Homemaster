"""Typer CLI entrypoint for Task Brain scenario runs."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from task_brain.graph import run_task_graph
from task_brain.trace import normalize_graph_trace_events, render_trace_report, write_trace_jsonl

app = typer.Typer(add_completion=False, help="Memory-grounded task brain CLI.")


@app.callback()
def main() -> None:
    """Task Brain command group."""


@app.command("run")
def run_command(
    scenario: Annotated[
        str,
        typer.Option(help="Scenario name under data/scenarios."),
    ],
    instruction: Annotated[
        str,
        typer.Option(help="User instruction for this run."),
    ],
    trace_jsonl: Annotated[
        Path | None,
        typer.Option("--trace-jsonl", help="Optional JSONL path to write normalized trace events."),
    ] = None,
) -> None:
    """Run one scenario and print a trace report."""
    result = run_task_graph(scenario=scenario, instruction=instruction)
    final_status = _as_status(result.get("final_status"))
    events = normalize_graph_trace_events(_as_trace(result.get("trace")))

    typer.echo(
        render_trace_report(
            scenario=scenario,
            instruction=instruction,
            final_status=final_status,
            events=events,
        )
    )

    if trace_jsonl is not None:
        write_trace_jsonl(
            path=trace_jsonl,
            scenario=scenario,
            final_status=final_status,
            events=events,
        )
        typer.echo(f"trace_jsonl: {trace_jsonl}")

    raise typer.Exit(code=0 if final_status == "success" else 1)


def _as_status(value: Any) -> str:
    if isinstance(value, str) and value:
        return value
    return "failed"


def _as_trace(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


if __name__ == "__main__":
    app()
