"""Interactive HomeMaster shell for Stage 07."""

from __future__ import annotations

from pathlib import Path

import typer

from homemaster.doctor import render_doctor_text, run_doctor
from homemaster.task_runner import DEFAULT_STAGE_07_RUNTIME_ROOT, run_homemaster_task


def run_interactive_shell() -> None:
    typer.echo("HomeMaster V1.2")
    report = run_doctor(live=False)
    if report.has_failures:
        typer.echo(render_doctor_text(report))
        typer.echo("本地体检存在 FAIL，先修复后再进入任务对话。")
        return
    typer.echo("输入自然语言任务，或输入 /doctor、/status、/debug、/exit。")
    last_debug_path: str | None = None
    last_status = "idle"
    while True:
        try:
            utterance = input("homemaster> ").strip()
        except EOFError:
            typer.echo("再见")
            return
        if not utterance:
            continue
        if utterance == "/exit":
            typer.echo("再见")
            return
        if utterance == "/doctor":
            typer.echo(render_doctor_text(run_doctor(live=False)))
            continue
        if utterance == "/status":
            typer.echo(f"status: {last_status}")
            continue
        if utterance == "/debug":
            typer.echo(f"debug: {last_debug_path or 'no task has run yet'}")
            continue

        scenario = _guess_scenario(utterance)
        run_id = f"interactive-{scenario}"
        typer.echo("Stage02 -> Stage06 running...")
        try:
            result = run_homemaster_task(
                utterance=utterance,
                scenario=scenario,
                runtime_memory_root=DEFAULT_STAGE_07_RUNTIME_ROOT,
                debug_root=Path("tests/homemaster/llm_cases"),
                run_id=run_id,
                live_models=False,
            )
        except Exception as exc:
            last_status = "failed"
            typer.echo(f"failed: {exc}")
            continue
        last_status = result.final_status
        last_debug_path = str(result.case_dir / "result.md")
        typer.echo(f"final_status: {result.final_status}")
        typer.echo(f"debug: {last_debug_path}")
        if result.memory_commit:
            typer.echo(f"task_record: {result.runtime_memory_root / 'task_records.jsonl'}")


def _guess_scenario(utterance: str) -> str:
    if "药" in utterance:
        return "check_medicine_success"
    if "找不到" in utterance or "不见" in utterance:
        return "object_not_found"
    return "fetch_cup_retry"
