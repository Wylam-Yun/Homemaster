from __future__ import annotations

from pathlib import Path

from homemaster.benchmarking.coworker_demo.config import load_coworker_config


def test_example_config_resolves_paths_against_project_root() -> None:
    config = load_coworker_config("config/coworker_demo.example.yaml")
    root = Path.cwd().resolve()
    assert config.paths.data_root == root / "data/coworker_demo/case_02"
    assert config.paths.artifact_root == root / "var/coworker-demo"
    assert config.paths.service_python == root / "apps/case02_openenv/.venv/bin/python"
    assert config.display.localhost_only is True
    assert config.browser.viewport_width == 1320
    assert config.browser.viewport_height == 900
    assert config.browser.window_x == 0
    assert config.browser.window_y == 96
    assert config.runtime.max_browser_actions == 64
