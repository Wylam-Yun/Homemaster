from __future__ import annotations

from pathlib import Path

from homemaster.scenario_runner import run_stage_07_scenario_matrix

SECRET_MARKERS = ("Authorization", "Bearer", "x-api-key", "api_keys", "sk-")


def test_stage_07_debug_assets_do_not_contain_secrets(tmp_path: Path) -> None:
    debug_root = tmp_path / "debug"
    run_stage_07_scenario_matrix(
        runtime_root=tmp_path / "runs",
        debug_root=debug_root,
        live_models=False,
        scenarios=["check_medicine_success"],
    )

    for path in debug_root.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".jsonl", ".md"}:
            text = path.read_text(encoding="utf-8")
            assert not any(marker in text for marker in SECRET_MARKERS), path
