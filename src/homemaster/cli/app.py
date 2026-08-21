"""Typer CLI entrypoint for HomeMaster."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from homemaster.benchmarking.alfworld.tracing import split_trace_bucket
from homemaster.cli.benchmark_alfworld import (
    handle_benchmark_alfworld,
    handle_benchmark_alfworld_taskset,
)
from homemaster.cli.benchmark_locomo import handle_benchmark_locomo
from homemaster.cli.child_worker import run_child_worker
from homemaster.cli.confirmation import CliPermissionMode
from homemaster.cli.cron_command import cron_app
from homemaster.cli.doctor import doctor_report_to_json, render_doctor_text, run_doctor
from homemaster.cli.dry_run import build_dry_run_preview
from homemaster.cli.errors import render_error_and_exit
from homemaster.cli.gateway_command import run_gateway
from homemaster.cli.interactive_shell import run_interactive_shell
from homemaster.cli.memory_command import memory_app
from homemaster.cli.renderers import parse_output_format, render_dry_run
from homemaster.cli.run_command import handle_print, handle_run
from homemaster.cli.session_command import session_app
from homemaster.config import load_config
from homemaster.events.logger import setup_logging
from homemaster.web.serve import run_web_server

app = typer.Typer(
    add_completion=False,
    help="HomeMaster V1.9 - Generic agent loop CLI.",
)
app.add_typer(session_app, name="session")
app.add_typer(cron_app, name="cron")
app.add_typer(memory_app, name="memory")


@app.command("child-worker", hidden=True)
def child_worker_command(
    model: Annotated[str | None, typer.Option("--model")] = None,
) -> None:
    raise typer.Exit(code=run_child_worker(model=model))


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Enable DEBUG logs and experience finalizer details."),
    ] = False,
    gateway: Annotated[
        bool,
        typer.Option(
            "--gateway",
            help="Run the configured Feishu/Lark Gateway without starting the interactive shell.",
        ),
    ] = False,
    alfworld: Annotated[
        bool,
        typer.Option(
            "--alfworld",
            help="Use the configured fixed ALFWorld environment for Gateway runs.",
        ),
    ] = False,
    browser: Annotated[
        bool,
        typer.Option(
            "--browser",
            help="Use the configured Ant browser environment for Gateway runs.",
        ),
    ] = False,
    config_path: Annotated[
        Path | None,
        typer.Option("--config", help="Path to the ignored HomeMaster YAML configuration."),
    ] = None,
    print_prompt: Annotated[
        str | None,
        typer.Option("--print", "-p", help="Print one response and exit."),
    ] = None,
    output_format: Annotated[
        str | None,
        typer.Option(
            "--output-format",
            help="Output format for print or dry-run: text, json, or stream-json.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview local resolution without external I/O."),
    ] = False,
    resume_session_id: Annotated[
        str | None,
        typer.Option("--resume", help="Resume the specified persisted session."),
    ] = None,
    continue_latest: Annotated[
        bool,
        typer.Option("--continue", help="Resume the latest persisted session."),
    ] = False,
    probe: Annotated[
        bool,
        typer.Option("--probe", help="Probe configured external discovery during dry-run."),
    ] = False,
    provider_name: Annotated[
        str | None,
        typer.Option("--provider-name", help="Select one configured chat provider."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override the selected provider model for this request."),
    ] = None,
    permission_mode: Annotated[
        CliPermissionMode | None,
        typer.Option(
            "--permission-mode",
            help="Interactive tool policy: full_auto, confirm, or plan.",
        ),
    ] = None,
) -> None:
    """Start interactive HomeMaster or execute one typed request."""

    if ctx.invoked_subcommand is not None:
        if permission_mode is not None:
            raise typer.BadParameter(
                "global --permission-mode is only valid without a subcommand; "
                "use 'homemaster shell --permission-mode ...'"
            )
        return
    try:
        if gateway:
            if alfworld and browser:
                raise typer.BadParameter("--alfworld and --browser are mutually exclusive")
            if any(
                (
                    print_prompt is not None,
                    output_format is not None,
                    dry_run,
                    resume_session_id is not None,
                    continue_latest,
                    probe,
                    provider_name is not None,
                    model is not None,
                    permission_mode is not None,
                )
            ):
                raise typer.BadParameter(
                    "--gateway cannot be combined with interactive, one-shot, or dry-run options"
                )
            run_gateway(
                load_config(config_path),
                environment="alfworld" if alfworld else "browser" if browser else None,
            )
            return
        if alfworld:
            raise typer.BadParameter("--alfworld requires --gateway")
        if browser:
            raise typer.BadParameter("--browser requires --gateway")
        if permission_mode is not None and (print_prompt is not None or dry_run):
            raise typer.BadParameter(
                "--permission-mode is only valid for the interactive shell"
            )
        if config_path is not None:
            raise typer.BadParameter("--config requires --gateway")
        resolved_format = parse_output_format(output_format)
        if resume_session_id is not None and continue_latest:
            raise typer.BadParameter("--continue cannot be combined with --resume")
        if probe and not dry_run:
            raise typer.BadParameter("--probe is only valid with --dry-run")
        if dry_run:
            prompt = print_prompt.strip() if print_prompt is not None else None
            if print_prompt is not None and not prompt:
                raise typer.BadParameter("-p/--print requires a non-empty prompt")
            preview = build_dry_run_preview(
                prompt=prompt,
                probe=probe,
                provider_name=provider_name,
                model=model,
            )
            typer.echo(render_dry_run(preview, resolved_format))
            return
        if print_prompt is not None:
            prompt = print_prompt.strip()
            if not prompt:
                raise typer.BadParameter("-p/--print requires a non-empty prompt")
            handle_print(
                prompt=prompt,
                output_format=resolved_format,
                resume_session_id=resume_session_id,
                continue_latest=continue_latest,
                provider_name=provider_name,
                model=model,
            )
            return
        if provider_name is not None or model is not None:
            raise typer.BadParameter("--provider-name/--model require --print or --dry-run")
        if output_format is not None:
            raise typer.BadParameter("--output-format requires --print or --dry-run")
        setup_logging(level="DEBUG" if debug else "INFO")
        run_interactive_shell(
            resume_session_id=resume_session_id,
            continue_latest=continue_latest,
            debug=debug,
            permission_mode=permission_mode or CliPermissionMode.FULL_AUTO,
        )
    except (typer.Exit, typer.BadParameter, SystemExit):
        raise
    except Exception as exc:
        render_error_and_exit(exc)


@app.command("run")
def run_command(
    utterance: Annotated[
        str | None,
        typer.Option("--utterance", help="Chinese user instruction to execute."),
    ] = None,
    world_path: Annotated[
        Path | None,
        typer.Option("--world", help="Optional world.json override."),
    ] = None,
    memory_path: Annotated[
        Path | None,
        typer.Option("--memory", help="Optional base memory.json override."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Stable run id for traces."),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="Logging level (DEBUG/INFO/WARNING/ERROR)."),
    ] = "INFO",
    progress: Annotated[
        bool,
        typer.Option("--progress/--no-progress", help="Show high-level progress events on stderr."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Show full thinking and tool result output on stderr."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet",
            help="Suppress console event output; stdout still prints the result.",
        ),
    ] = False,
    resume_session_id: Annotated[
        str | None,
        typer.Option("--resume", help="Resume the specified persisted session id."),
    ] = None,
    continue_latest: Annotated[
        bool,
        typer.Option("--continue", help="Resume the latest persisted session."),
    ] = False,
    provider_name: Annotated[
        str | None,
        typer.Option("--provider-name", help="Select one configured chat provider."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override the selected provider model for this run."),
    ] = None,
) -> None:
    """Run one HomeMaster task with the generic agent loop."""
    try:
        handle_run(
            utterance=utterance,
            world_path=world_path,
            memory_path=memory_path,
            run_id=run_id,
            log_level=log_level,
            progress=progress,
            verbose=verbose,
            quiet=quiet,
            resume_session_id=resume_session_id,
            continue_latest=continue_latest,
            provider_name=provider_name,
            model=model,
        )
    except Exception as exc:
        render_error_and_exit(exc)


@app.command("doctor")
def doctor_command(
    live: Annotated[
        bool,
        typer.Option("--live", help="Run live chat and MemoryEmbedding provider smoke checks."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Check HomeMaster local environment and optional live providers."""
    try:
        setup_logging()
        report = run_doctor(live=live)
        if json_output:
            typer.echo(doctor_report_to_json(report))
        else:
            typer.echo(render_doctor_text(report))
        if report.has_failures:
            raise typer.Exit(code=1)
    except (typer.Exit, SystemExit):
        raise
    except Exception as exc:
        render_error_and_exit(exc)


@app.command("shell")
def shell_command(
    resume_session_id: Annotated[
        str | None,
        typer.Option("--resume", help="Resume the specified persisted session id."),
    ] = None,
    permission_mode: Annotated[
        CliPermissionMode,
        typer.Option(
            "--permission-mode",
            help="Interactive tool policy: full_auto, confirm, or plan.",
        ),
    ] = CliPermissionMode.FULL_AUTO,
) -> None:
    """Launch the interactive HomeMaster shell."""
    run_interactive_shell(
        resume_session_id=resume_session_id,
        permission_mode=permission_mode,
    )


@app.command("gateway")
def gateway_command(
    config_path: Annotated[
        Path | None,
        typer.Option("--config", help="Path to the ignored HomeMaster YAML configuration."),
    ] = None,
) -> None:
    """Run the configured Feishu/Lark WebSocket Gateway."""
    try:
        run_gateway(load_config(config_path))
    except (typer.Exit, SystemExit):
        raise
    except Exception as exc:
        render_error_and_exit(exc)


@app.command("serve")
def serve_command(
    host: Annotated[
        str,
        typer.Option("--host", help="Loopback address for the local Web Console."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", min=1, max=65535, help="Local Web Console port."),
    ] = 8000,
    alfworld: Annotated[
        bool,
        typer.Option(
            "--alfworld",
            help="Use the configured fixed ALFWorld environment in the Web Console.",
        ),
    ] = False,
) -> None:
    """Run the loopback-only HomeMaster Web Console."""

    try:
        run_web_server(
            host=host,
            port=port,
            environment="alfworld" if alfworld else None,
        )
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc
    except (typer.Exit, SystemExit):
        raise
    except Exception as exc:
        render_error_and_exit(exc)


@app.command("benchmark-alfworld")
def benchmark_alfworld_command(
    alfworld_root: Annotated[
        Path,
        typer.Option("--alfworld-root", help="Path to the local ALFWorld repository."),
    ],
    alfworld_config: Annotated[
        Path,
        typer.Option("--alfworld-config", help="Path to ALFWorld YAML config."),
    ],
    trace_root: Annotated[
        Path,
        typer.Option("--trace-root", help="Output directory for benchmark traces."),
    ] = Path("/tmp/homemaster/alfworld"),
    env_type: Annotated[
        str,
        typer.Option("--env-type", help="ALFWorld environment type."),
    ] = "AlfredTWEnv",
    split: Annotated[
        str,
        typer.Option("--split", help="train, valid_seen, or valid_unseen."),
    ] = "valid_seen",
    episodes: Annotated[
        int,
        typer.Option("--episodes", help="Number of episodes to run."),
    ] = 1,
    memory_mode: Annotated[
        str,
        typer.Option("--memory-mode", help="disabled, readonly, or full."),
    ] = "disabled",
    max_invalid_actions: Annotated[
        int,
        typer.Option("--max-invalid-actions", help="Fail after this many invalid actions."),
    ] = 100,
    max_env_steps: Annotated[
        int,
        typer.Option(
            "--max-env-steps",
            help="Fail after this many ALFWorld environment action steps.",
        ),
    ] = 50,
    max_tool_iterations: Annotated[
        int,
        typer.Option("--max-tool-iterations", help="Maximum HomeMaster tool iterations."),
    ] = 1000,
    provider_config: Annotated[
        Path | None,
        typer.Option("--api-config", help="Optional provider config JSON override."),
    ] = None,
    provider_name: Annotated[
        str | None,
        typer.Option(
            "--provider-name",
            help="Optional provider name override; defaults to the API config default.",
        ),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Stable benchmark run id."),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="Logging level."),
    ] = "INFO",
    observation_mode: Annotated[
        str,
        typer.Option("--observation-mode", help="visual_eval or textual_debug."),
    ] = "visual_eval",
    trial_manifest: Annotated[
        Path | None,
        typer.Option(
            "--trial-manifest",
            help="Required ordered trial-selection manifest for AlfredThorEnv runs.",
        ),
    ] = None,
) -> None:
    """Run HomeMaster on ALFWorld benchmark episodes."""
    try:
        summary = handle_benchmark_alfworld(
            alfworld_root=alfworld_root,
            alfworld_config=alfworld_config,
            trace_root=trace_root,
            env_type=env_type,
            split=split,
            episodes=episodes,
            memory_mode=memory_mode,
            max_invalid_actions=max_invalid_actions,
            max_env_steps=max_env_steps,
            max_tool_iterations=max_tool_iterations,
            provider_config=provider_config,
            provider_name=provider_name,
            run_id=run_id,
            log_level=log_level,
            observation_mode=observation_mode,
            trial_manifest=trial_manifest,
        )
        metrics = summary.to_dict()
        typer.echo(f"run_id: {summary.run_id}")
        typer.echo(f"episodes: {len(summary.episodes)}")
        typer.echo(f"success_rate: {summary.success_rate:.3f}")
        typer.echo(f"raw_success_rate: {float(metrics['raw_success_rate']):.3f}")
        typer.echo(f"agent_scored_episodes: {metrics['agent_scored_episodes']}")
        typer.echo(
            f"agent_success_rate_on_valid: {float(metrics['agent_success_rate_on_valid']):.3f}"
        )
        typer.echo(f"harness_invalid_episodes: {metrics['harness_invalid_episodes']}")
        typer.echo(f"harness_valid_coverage: {float(metrics['harness_valid_coverage']):.3f}")
        typer.echo(f"evaluation_valid_coverage: {float(metrics['evaluation_valid_coverage']):.3f}")
        typer.echo(f"harness_coverage: {float(metrics['harness_coverage']):.3f}")
        typer.echo(f"provider_availability: {float(metrics['provider_availability']):.3f}")
        typer.echo(f"runtime_availability: {float(metrics['runtime_availability']):.3f}")
        typer.echo(f"cancelled_episodes: {int(metrics['cancelled_episodes'])}")
        typer.echo(
            f"formal_score_available: {str(bool(metrics['formal_score_available'])).lower()}"
        )
        typer.echo(f"trace_root: {trace_root / split_trace_bucket(split) / summary.run_id}")
    except Exception as exc:
        render_error_and_exit(exc)


@app.command("benchmark-locomo")
def benchmark_locomo_command(
    data_file: Annotated[
        Path,
        typer.Option("--data-file", help="Path to LoCoMo locomo10.json."),
    ] = Path("../locomo/data/locomo10.json"),
    sample_id: Annotated[
        str,
        typer.Option("--sample-id", help="LoCoMo conversation sample id."),
    ] = "conv-26",
    focal_speaker: Annotated[
        str,
        typer.Option("--focal-speaker", help="Person name used as the memory user id."),
    ] = "Caroline",
    max_source_turns: Annotated[
        int,
        typer.Option("--max-source-turns", help="Maximum original dialogue turns to ingest."),
    ] = 100,
    qa_probes: Annotated[
        int,
        typer.Option("--qa-probes", help="Answerable LoCoMo questions to run without scoring."),
    ] = 10,
    run_deadline_seconds: Annotated[
        float,
        typer.Option(
            "--run-deadline-seconds",
            help="Maximum wall time for each HomeMaster source or QA run.",
        ),
    ] = 300.0,
    trace_root: Annotated[
        Path,
        typer.Option("--trace-root", help="Output directory for LoCoMo benchmark runs."),
    ] = Path("/tmp/homemaster/locomo"),
    memory_data_root: Annotated[
        Path | None,
        typer.Option(
            "--memory-data-root",
            help="Separate MindMemOS data root to avoid an embedded Qdrant lock conflict.",
        ),
    ] = None,
    config_path: Annotated[
        Path | None,
        typer.Option("--config", help="Path to the ignored HomeMaster YAML configuration."),
    ] = None,
    provider_name: Annotated[
        str | None,
        typer.Option("--provider-name", help="Optional configured chat provider name."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Optional chat model override."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Stable output run id; directory must not exist."),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="Logging level."),
    ] = "INFO",
) -> None:
    """Replay LoCoMo through HomeMaster memory, feedback, and dreaming."""
    try:
        summary = handle_benchmark_locomo(
            data_file=data_file,
            sample_id=sample_id,
            focal_speaker=focal_speaker,
            max_source_turns=max_source_turns,
            qa_probes=qa_probes,
            run_deadline_seconds=run_deadline_seconds,
            trace_root=trace_root,
            memory_data_root=memory_data_root,
            config_path=config_path,
            provider_name=provider_name,
            model_override=model,
            run_id=run_id,
            log_level=log_level,
        )
        typer.echo(f"run_id: {summary['run_id']}")
        typer.echo(f"status: {summary['status']}")
        typer.echo(f"sample_id: {summary['sample_id']}")
        typer.echo(f"focal_speaker: {summary['focal_speaker']}")
        typer.echo(f"source_turns: {summary['source_turn_count']}")
        typer.echo(f"source_sessions: {summary['source_session_count']}")
        typer.echo(f"qa_probes: {summary['qa_probe_count']}")
        typer.echo(
            "feature_counts: "
            + json.dumps(summary["feature_counts"], ensure_ascii=False)
        )
        summary_path = trace_root.expanduser().resolve() / str(summary["run_id"]) / "summary.json"
        typer.echo(f"summary: {summary_path}")
    except Exception as exc:
        render_error_and_exit(exc)


@app.command("benchmark-alfworld-taskset")
def benchmark_alfworld_taskset_command(
    taskset_config: Annotated[
        Path,
        typer.Option(
            "--taskset-config",
            help="Path to alfworld_tasksets.yaml (long-horizon task chain definition).",
        ),
    ],
    alfworld_root: Annotated[
        Path,
        typer.Option("--alfworld-root", help="Path to the local ALFWorld repository."),
    ],
    alfworld_config: Annotated[
        Path,
        typer.Option("--alfworld-config", help="Path to ALFWorld YAML config (eval_config.yaml)."),
    ],
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="Logging level."),
    ] = "INFO",
) -> None:
    """Run HomeMaster on long-horizon ALFWorld tasksets (one persistent scene per taskset)."""
    try:
        summary = handle_benchmark_alfworld_taskset(
            taskset_config=taskset_config,
            alfworld_root=alfworld_root,
            alfworld_config=alfworld_config,
            log_level=log_level,
        )
        metrics = summary.to_dict()
        typer.echo(f"run_id: {summary.run_id}")
        typer.echo(f"tasksets: {len(summary.taskset_results)}")
        typer.echo(f"agent_scored_tasksets: {metrics['agent_scored_tasksets']}")
        typer.echo(
            f"agent_success_rate_on_valid: {float(metrics['agent_success_rate_on_valid']):.3f}"
        )
        typer.echo(f"harness_invalid_tasksets: {metrics['harness_invalid_tasksets']}")
        typer.echo(f"harness_valid_coverage: {float(metrics['harness_valid_coverage']):.3f}")
        typer.echo(
            f"formal_score_available: {str(bool(metrics['formal_score_available'])).lower()}"
        )
        typer.echo(f"not_run_subtasks: {metrics['not_run_subtasks']}")
        for ts in summary.taskset_results:
            typer.echo(
                f"  [{ts.difficulty}] {ts.taskset_id} (FloorPlan{ts.floorplan}): "
                f"chain_success={ts.chain_success} "
                f"subtask_success_rate={ts.success_rate:.3f} "
                f"chain_completed={ts.chain_completed_count}/{len(ts.subtasks)}"
            )
    except Exception as exc:
        render_error_and_exit(exc)


if __name__ == "__main__":
    app()
