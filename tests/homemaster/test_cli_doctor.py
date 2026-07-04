"""Tests for CLI doctor command."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from homemaster.cli.app import app
from homemaster.cli.doctor import run_doctor

SECRET_MARKERS = ("Authorization", "Bearer", "x-api-key", "api_keys", "sk-")


def test_doctor_local_report_runs_without_live_api() -> None:
    report = run_doctor(live=False)

    payload = report.model_dump()
    assert payload["live"] is False
    assert payload["checks"]
    assert any(check["name"] == "config_source" for check in payload["checks"])
    assert payload["config_source"] == "config/homemaster.yaml"
    encoded = json.dumps(payload, ensure_ascii=False)
    assert not any(marker in encoded for marker in SECRET_MARKERS)


def test_cli_doctor_json_is_parseable_and_sanitized() -> None:
    result = CliRunner().invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["checks"]
    encoded = json.dumps(payload, ensure_ascii=False)
    assert not any(marker in encoded for marker in SECRET_MARKERS)


def test_cli_doctor_text_reports_pass_warn_fail() -> None:
    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0, result.stdout
    assert "HomeMaster Doctor" in result.stdout
    assert any(status in result.stdout for status in ("PASS", "WARN", "FAIL"))
    assert not any(marker in result.stdout for marker in SECRET_MARKERS)
