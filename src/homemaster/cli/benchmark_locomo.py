"""CLI handler for the end-to-end LoCoMo pilot."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homemaster.benchmarking.locomo import LocomoBenchmarkConfig, LocomoBenchmarkRunner
from homemaster.config import MemoryMigrationSpec, load_config
from homemaster.events.logger import setup_logging


def handle_benchmark_locomo(
    *,
    data_file: Path,
    sample_id: str,
    focal_speaker: str,
    max_source_turns: int,
    qa_probes: int,
    run_deadline_seconds: float,
    trace_root: Path,
    memory_data_root: Path | None = None,
    config_path: Path | None = None,
    provider_name: str | None = None,
    model_override: str | None = None,
    run_id: str | None = None,
    log_level: str = "INFO",
) -> dict[str, Any]:
    setup_logging(level=log_level)
    home_config = load_config(config_path)
    if memory_data_root is not None:
        isolated_root = memory_data_root.expanduser().resolve()
        home_config = home_config.model_copy(
            update={
                "memory": home_config.memory.model_copy(
                    update={
                        "data_root": isolated_root,
                        "migration_spec": MemoryMigrationSpec(
                            files_source=isolated_root / "_empty_legacy_files",
                            evidence_source=isolated_root / "_empty_legacy_evidence.sqlite3",
                        ),
                    }
                )
            }
        )
    return LocomoBenchmarkRunner(
        LocomoBenchmarkConfig(
            data_file=data_file,
            sample_id=sample_id,
            focal_speaker=focal_speaker,
            max_source_turns=max_source_turns,
            qa_probes=qa_probes,
            run_deadline_seconds=run_deadline_seconds,
            trace_root=trace_root,
            home_config=home_config,
            provider_name=provider_name,
            model_override=model_override,
            run_id=run_id,
        )
    ).run()


__all__ = ["handle_benchmark_locomo"]
