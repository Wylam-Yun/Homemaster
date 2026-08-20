from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.setup_memory_runtime import (
    RuntimeSetupError,
    check_runtime,
    initialize_runtime,
)


def _executable(path: Path, content: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def _private_config(path: Path) -> Path:
    path.parent.mkdir(parents=True)
    path.write_text(
        """
providers:
  default: Mimo
  items: []
memory:
  enabled: true
  data_root: /foreign/server/memory
  neo4j:
    mode: managed_local
    home: /foreign/server/neo4j
    java_home: /foreign/server/java
    password: private-password
""",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _runtime_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    neo4j = tmp_path / "installs" / "neo4j"
    java = tmp_path / "installs" / "java"
    python = tmp_path / "env" / "bin" / "python"
    _executable(neo4j / "bin" / "neo4j")
    _executable(neo4j / "bin" / "neo4j-admin")
    _executable(java / "bin" / "java")
    _executable(python)
    return neo4j, java, python


def test_initialize_runtime_writes_portable_config_and_preserves_existing_memory(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "Homemaster"
    config = _private_config(repo / "config" / "homemaster.yaml")
    neo4j, java, python = _runtime_inputs(tmp_path)
    existing_memory = tmp_path / "existing-memory"
    existing_memory.mkdir()
    (existing_memory / "sentinel.txt").write_text("keep", encoding="utf-8")

    result = initialize_runtime(
        repo_root=repo,
        config_path=config,
        neo4j_home=neo4j,
        java_home=java,
        python_executable=python,
        memory_home=existing_memory,
        validate_python=False,
    )

    payload = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert payload["memory"]["data_root"] == "../.runtime/memory"
    assert payload["memory"]["neo4j"]["mode"] == "managed_local"
    assert payload["memory"]["neo4j"]["home"] == "../.runtime/neo4j"
    assert payload["memory"]["neo4j"]["java_home"] == "../.runtime/java"
    assert payload["memory"]["neo4j"]["password"] == "private-password"
    assert config.stat().st_mode & 0o777 == 0o600
    assert (repo / ".runtime" / "memory").resolve() == existing_memory.resolve()
    assert (repo / ".runtime" / "neo4j").resolve() == neo4j.resolve()
    assert (repo / ".runtime" / "java").resolve() == java.resolve()
    assert (repo / ".runtime" / "venv").resolve() == python.parent.parent.resolve()
    assert (existing_memory / "sentinel.txt").read_text(encoding="utf-8") == "keep"
    assert result["status"] == "ready"
    assert result["alfworld_ready"] is False


def test_initialize_runtime_is_idempotent_and_check_is_read_only(tmp_path: Path) -> None:
    repo = tmp_path / "Homemaster"
    config = _private_config(repo / "config" / "homemaster.yaml")
    neo4j, java, python = _runtime_inputs(tmp_path)

    first = initialize_runtime(
        repo_root=repo,
        config_path=config,
        neo4j_home=neo4j,
        java_home=java,
        python_executable=python,
        validate_python=False,
    )
    before = config.read_bytes()
    second = initialize_runtime(
        repo_root=repo,
        config_path=config,
        neo4j_home=neo4j,
        java_home=java,
        python_executable=python,
        validate_python=False,
    )
    checked = check_runtime(repo_root=repo, config_path=config, validate_python=False)

    assert first == second == checked
    assert config.read_bytes() == before


def test_initialize_runtime_refuses_conflicting_existing_binding(tmp_path: Path) -> None:
    repo = tmp_path / "Homemaster"
    config = _private_config(repo / "config" / "homemaster.yaml")
    neo4j, java, python = _runtime_inputs(tmp_path)
    runtime = repo / ".runtime"
    runtime.mkdir(parents=True)
    (runtime / "neo4j").mkdir()

    with pytest.raises(RuntimeSetupError, match="conflicts with requested target"):
        initialize_runtime(
            repo_root=repo,
            config_path=config,
            neo4j_home=neo4j,
            java_home=java,
            python_executable=python,
            validate_python=False,
        )


def test_check_runtime_rejects_foreign_absolute_config_path(tmp_path: Path) -> None:
    repo = tmp_path / "Homemaster"
    config = _private_config(repo / "config" / "homemaster.yaml")
    repo.joinpath(".runtime").mkdir(parents=True)

    with pytest.raises(RuntimeSetupError, match="memory.data_root must be"):
        check_runtime(repo_root=repo, config_path=config, validate_python=False)


def test_launcher_uses_bound_python_and_config_from_unrelated_cwd(tmp_path: Path) -> None:
    repo = tmp_path / "Homemaster"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    source_launcher = Path(__file__).resolve().parents[2] / "scripts" / "homemaster"
    launcher = scripts / "homemaster"
    launcher.write_bytes(source_launcher.read_bytes())
    launcher.chmod(0o755)
    config = _private_config(repo / "config" / "homemaster.yaml")
    runtime = repo / ".runtime"
    runtime.mkdir()
    capture = tmp_path / "capture.json"
    fake_python = _executable(
        tmp_path / "fake-python",
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then exit 0; fi\n"
        "if [ \"$2\" = \"scripts/setup_memory_runtime.py\" ]; then exit 0; fi\n"
        f"printf '%s\\n' \"{{\\\"cwd\\\":\\\"$PWD\\\",\\\"config\\\":"
        f"\\\"$HOMEMASTER_CONFIG_PATH\\\",\\\"pythonpath\\\":\\\"$PYTHONPATH\\\","
        f"\\\"args\\\":\\\"$*\\\"}}\" > {capture}\n",
    )
    fake_venv = tmp_path / "fake-venv"
    fake_venv.joinpath("bin").mkdir(parents=True)
    fake_venv.joinpath("bin", "python").symlink_to(fake_python)
    (runtime / "venv").symlink_to(fake_venv)
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()

    completed = subprocess.run(
        [str(launcher), "doctor", "--json"],
        cwd=unrelated,
        text=True,
        capture_output=True,
        env={**os.environ, "HOMEMASTER_SKIP_RUNTIME_CHECK": "1"},
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    recorded = json.loads(capture.read_text(encoding="utf-8"))
    assert recorded["cwd"] == str(repo)
    assert recorded["config"] == str(config)
    assert recorded["pythonpath"] == str(repo / "src")
    assert recorded["args"] == "-m homemaster.cli doctor --json"
