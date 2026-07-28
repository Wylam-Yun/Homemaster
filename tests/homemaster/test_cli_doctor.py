"""Tests for CLI doctor command."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from homemaster.cli.app import app
from homemaster.cli.doctor import run_doctor
from homemaster.memory.file_store import FileMemoryStore
from homemaster.memory.mem0_store import Mem0MemoryStore


@pytest.fixture(autouse=True)
def _use_test_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
          root: {tmp_path / "memory-files"}
          mem0:
            qdrant_path: {tmp_path / "qdrant"}
            history_db_path: {tmp_path / "history.sqlite3"}
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
    assert memory["details"]["available"] is True
    assert memory["details"]["fastembed_cache_path"].endswith(".cache/homemaster/fastembed")
    assert payload["config_source"] == "config/homemaster.yaml"
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "doctor-chat-secret" in encoded
    assert "doctor-embedding-secret" in encoded


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


def test_doctor_reports_qdrant_lock_conflict_while_file_memory_remains_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from homemaster.cli import doctor as doctor_module

    config = doctor_module.load_config(doctor_module.HOMEMASTER_CONFIG_PATH)
    file_store = FileMemoryStore(config.memory)
    file_store.start()
    owner = Mem0MemoryStore(config)
    owner.start()
    assert owner.available
    monkeypatch.setattr("homemaster.cli.doctor.load_config", lambda *_args, **_kwargs: config)
    try:
        report = run_doctor(live=False)

        memory = next(check for check in report.checks if check.name == "memory_backend")
        assert memory.status == "WARN"
        assert memory.details["available"] is False
        assert "already accessed by another instance" in str(memory.details["cause"]).casefold()
        assert file_store.read("memory").target == "memory"
    finally:
        asyncio.run(owner.close())
