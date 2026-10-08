"""Tests for CLI doctor command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from homemaster.cli.app import app
from homemaster.cli.doctor import render_doctor_text, run_doctor


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
          # Explicit full tier: the fixture exercises the MindMemOS check
          # surface, while the lightweight default tier is "files".
          mode: full
          data_root: {tmp_path / "memory-data"}
          embedding_dimensions: 8
          neo4j:
            mode: managed_local
            home: {tmp_path / "neo4j-home"}
            java_home: {tmp_path / "java-home"}
            uri: bolt://127.0.0.1:7687
            username: neo4j
            password: doctor-neo4j-secret
            database: neo4j
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "homemaster.cli.doctor.resolve_config_path", lambda _p=None: config_path
    )
    monkeypatch.setattr(
        "homemaster.cli.doctor._config_source", lambda _path: "config/homemaster.yaml"
    )


def test_doctor_local_report_runs_without_live_api() -> None:
    report = run_doctor(live=False)
    payload = report.model_dump()
    assert payload["live"] is False
    assert payload["checks"]
    assert any(check["name"] == "config_source" for check in payload["checks"])
    memory = next(check for check in payload["checks"] if check["name"] == "memory_backend")
    assert memory["status"] == "PASS"
    assert memory["details"]["probe"] == "not_opened"
    assert memory["details"]["qdrant_path"].endswith("mindmemos/qdrant")
    assert memory["details"]["neo4j_mode"] == "managed_local"
    assert memory["details"]["neo4j_uri"] == "bolt://127.0.0.1:7687"
    assert memory["details"]["neo4j_home"].endswith("neo4j-home")
    assert memory["details"]["java_home"].endswith("java-home")
    assert payload["config_source"] == "config/homemaster.yaml"
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "doctor-chat-secret" not in encoded
    assert "doctor-embedding-secret" not in encoded
    assert '"api_keys_configured": true' in encoded
    assert '"api_key_count": 1' in encoded
    assert "doctor-neo4j-secret" not in encoded


def test_doctor_accepts_v35_runtime_venv(monkeypatch: pytest.MonkeyPatch) -> None:
    from homemaster.cli import doctor as doctor_module

    monkeypatch.setattr(doctor_module.sys, "executable", "/repo/.runtime/venv/bin/python")
    check = doctor_module._python_environment_check()

    assert check.status == "PASS"


def test_doctor_ignores_global_legacy_files_for_explicit_data_root(
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
    assert memory.status == "PASS"
    assert memory.details["probe"] == "not_opened"
    assert before == after
    assert not target.exists()


def test_cli_doctor_json_is_parseable_and_preserves_authoritative_config() -> None:
    result = CliRunner().invoke(app, ["doctor", "--json"])

    assert result.exit_code in {0, 1}, result.stdout
    payload = json.loads(result.stdout)
    assert payload["checks"]
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "doctor-chat-secret" not in encoded
    assert "doctor-embedding-secret" not in encoded
    assert "doctor-neo4j-secret" not in encoded


def test_cli_doctor_text_reports_pass_warn_fail() -> None:
    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code in {0, 1}, result.stdout
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


def test_doctor_checks_embedded_mindmemos_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from homemaster.cli import doctor as doctor_module

    events: list[str] = []
    original_import = doctor_module.importlib.import_module

    def tracked_import(name: str):
        if name == "mindmemos":
            events.append("import")
        return original_import(name)

    monkeypatch.setattr(doctor_module.importlib, "import_module", tracked_import)

    checks = doctor_module._import_checks()

    assert next(check for check in checks if check.name == "import:mindmemos").status == "PASS"
    assert events == ["import"]


def test_doctor_files_tier_reports_mode_and_skips_full_tier_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """files mode must pass on a lightweight install without the memory extra."""

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
        memory:
          mode: files
          data_root: {tmp_path / "memory-data"}
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "homemaster.cli.doctor.resolve_config_path", lambda _p=None: config_path
    )

    report = run_doctor(live=False)

    names = {check.name for check in report.checks}
    assert "memory_mode" in names
    memory_mode = next(check for check in report.checks if check.name == "memory_mode")
    assert memory_mode.status == "PASS"
    assert "memory.mode=files" in memory_mode.message
    # Full-tier checks must not run at all in files mode.
    assert "mindmemos_runtime" not in names
    assert "embedding_endpoint" not in names
    assert "memory_backend" not in names
    for heavy in ("jieba", "mindmemos", "qdrant_client", "neo4j", "spacy"):
        assert f"import:{heavy}" not in names
    assert "import:homemaster" in names
    # The embedding provider is optional in files mode and must not fail config.
    assert next(check for check in report.checks if check.name == "config_source").status == "PASS"
    assert not report.has_failures


def test_doctor_files_tier_text_output_names_the_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "homemaster.yaml"
    config_path.write_text("memory:\n  mode: files\n", encoding="utf-8")
    monkeypatch.setattr(
        "homemaster.cli.doctor.resolve_config_path", lambda _p=None: config_path
    )

    report = run_doctor(live=False)
    text = render_doctor_text(report)
    assert "memory.mode=files" in text


def test_doctor_full_tier_keeps_mindmemos_and_backend_checks() -> None:
    """The default full tier preserves the existing check surface."""

    report = run_doctor(live=False)

    names = {check.name for check in report.checks}
    memory_mode = next(check for check in report.checks if check.name == "memory_mode")
    assert "memory.mode=full" in memory_mode.message
    assert "mindmemos_runtime" in names
    assert "memory_backend" in names
    assert "embedding_endpoint" in names
    assert "import:mindmemos" in names


def test_doctor_default_report_skips_optional_alfworld_checks() -> None:
    report = run_doctor(live=False)

    names = {check.name for check in report.checks}
    assert "alfworld_binding" not in names
    assert "alfworld_worker_ipc" not in names


def test_doctor_alfworld_checks_are_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    from homemaster.cli import doctor as doctor_module

    calls: list[str] = []

    def binding_check() -> doctor_module.DoctorCheck:
        calls.append("binding")
        return doctor_module.DoctorCheck(
            name="alfworld_binding",
            status="PASS",
            message="binding ok",
        )

    def worker_check() -> doctor_module.DoctorCheck:
        calls.append("worker")
        return doctor_module.DoctorCheck(
            name="alfworld_worker_ipc",
            status="PASS",
            message="worker ok",
        )

    monkeypatch.setattr(doctor_module, "_alfworld_binding_check", binding_check)
    monkeypatch.setattr(doctor_module, "_worker_protocol_check", worker_check)

    run_doctor(live=False)
    assert calls == []

    run_doctor(live=False, alfworld=True)
    assert calls == ["binding", "worker"]


def test_cli_doctor_alfworld_flag_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    from homemaster.cli.doctor import DoctorReport

    app_module = importlib.import_module("homemaster.cli.app")
    received: dict[str, bool] = {}

    def fake_run_doctor(
        *, live: bool = False, alfworld: bool = False, config_path=None
    ) -> DoctorReport:
        received["live"] = live
        received["alfworld"] = alfworld
        received["config_path"] = config_path
        return DoctorReport(live=live, config_source="test", checks=[])

    monkeypatch.setattr(app_module, "run_doctor", fake_run_doctor)

    result = CliRunner().invoke(app, ["doctor", "--alfworld", "--json"])

    assert result.exit_code == 0, result.stdout
    assert received == {"live": False, "alfworld": True, "config_path": None}


def test_cli_doctor_config_flag_is_forwarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import importlib

    from homemaster.cli.doctor import DoctorReport

    app_module = importlib.import_module("homemaster.cli.app")
    config_path = tmp_path / "homemaster.yaml"
    received: dict[str, object] = {}

    def fake_run_doctor(
        *, live: bool = False, alfworld: bool = False, config_path=None
    ) -> DoctorReport:
        received["config_path"] = config_path
        return DoctorReport(live=live, config_source="test", checks=[])

    monkeypatch.setattr(app_module, "run_doctor", fake_run_doctor)

    result = CliRunner().invoke(
        app, ["doctor", "--config", str(config_path), "--json"]
    )

    assert result.exit_code == 0, result.stdout
    assert received["config_path"] == config_path
