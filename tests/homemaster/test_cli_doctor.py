"""Tests for CLI doctor command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from homemaster.cli.app import app
from homemaster.cli.doctor import run_doctor


@pytest.fixture(autouse=True)
def _use_test_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path = tmp_path / "homemaster.yaml"
    config_path.write_text(
        f"""
        providers:
          default: Mimo
          items:
            - name: Mimo
              kind: chat
              api_format: anthropic
              transport: anthropic_sdk
              base_url: https://mimo.example/anthropic
              model: mimo-v2.5
              api_keys: [doctor-chat-secret]
            - name: MemoryEmbedding
              kind: embedding
              api_format: openai
              transport: openai_sdk
              base_url: https://embedding.example/v1
              model: BAAI/bge-m3
              embedding_url: https://embedding.example/v1/embeddings
              api_keys: [doctor-embedding-secret]
        memory:
          data_root: {tmp_path / "memory-data"}
          mem0:
            embedding_dimensions: 8
            collection_name: doctor_memory_8_v1
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr("homemaster.cli.doctor.HOMEMASTER_CONFIG_PATH", config_path)
    monkeypatch.setattr("homemaster.cli.doctor._config_source", lambda: "config/homemaster.yaml")


def test_doctor_local_report_runs_without_live_api() -> None:
    report = run_doctor(live=False)

    payload = report.model_dump()
    assert payload["live"] is False
    assert payload["checks"]
    assert any(check["name"] == "config_source" for check in payload["checks"])
    memory = next(check for check in payload["checks"] if check["name"] == "memory_backend")
    assert memory["status"] == "PASS"
    assert memory["details"]["probe"] == "not_opened"
    assert memory["details"]["fastembed_cache_path"].endswith(".cache/homemaster/fastembed")
    assert payload["config_source"] == "config/homemaster.yaml"
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "doctor-chat-secret" in encoded
    assert "doctor-embedding-secret" in encoded


def test_doctor_reports_migration_required_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / ".homemaster" / "memories"
    source.mkdir(parents=True)
    for name in ("SOUL.md", "USER.md", "MEMORY.md"):
        source.joinpath(name).write_text(name, encoding="utf-8")
    target = tmp_path / "memory-data"
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    report = run_doctor(live=False)

    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    memory = next(check for check in report.checks if check.name == "memory_backend")
    assert memory.status == "WARN"
    assert memory.details["migration_status"] == "migration_required"
    assert "migration_required" in memory.message
    assert before == after
    assert not target.exists()


def test_cli_doctor_json_is_parseable_and_preserves_authoritative_config() -> None:
    result = CliRunner().invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["checks"]
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "doctor-chat-secret" in encoded
    assert "doctor-embedding-secret" in encoded


def test_cli_doctor_text_reports_pass_warn_fail() -> None:
    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0, result.stdout
    assert "HomeMaster Doctor" in result.stdout
    assert any(status in result.stdout for status in ("PASS", "WARN", "FAIL"))
    assert "api_keys" not in result.stdout


def test_doctor_ready_state_does_not_materialize_backend_or_cache(
    tmp_path: Path,
) -> None:
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    report = run_doctor(live=False)

    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    memory = next(check for check in report.checks if check.name == "memory_backend")
    assert memory.status == "PASS"
    assert memory.details["probe"] == "not_opened"
    assert before == after
    assert not (tmp_path / "memory-data").exists()


def test_doctor_verifies_vendored_mem0_before_package_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from homemaster.cli import doctor as doctor_module

    events: list[str] = []
    original_import = doctor_module.importlib.import_module

    def verify() -> Path:
        events.append("verify")
        return Path("/verified/mem0")

    def tracked_import(name: str):
        if name == "mem0":
            events.append("import")
        return original_import(name)

    monkeypatch.setattr(doctor_module, "verify_vendored_mem0", verify)
    monkeypatch.setattr(doctor_module.importlib, "import_module", tracked_import)

    checks = doctor_module._import_checks()

    assert next(check for check in checks if check.name == "import:mem0").status == "PASS"
    assert events == ["verify"]
