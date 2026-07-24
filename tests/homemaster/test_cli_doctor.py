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
    config_path = tmp_path / "homemaster.yaml"
    config_path.write_text(
        """
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
