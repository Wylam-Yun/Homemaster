from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_built_wheel_exposes_builtin_skills_outside_source_checkout(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[3]
    dist = tmp_path / "dist"
    venv = tmp_path / "venv"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist)],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(dist.glob("homemaster-*.whl"))
    subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(venv)],
        check=True,
        capture_output=True,
        text=True,
    )
    python = venv / "bin" / "python"
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), "--no-deps", str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )
    probe = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            (
                "from importlib.resources import files; "
                "r=files('homemaster.skills').joinpath('builtin'); "
                "assert r.joinpath('fetch_object','SKILL.md').is_file(); "
                "assert r.joinpath('check_object_state','SKILL.md').is_file(); "
                "print('PASS')"
            ),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert probe.stdout.strip() == "PASS"
