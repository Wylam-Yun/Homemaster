"""Stage 07 environment doctor for the HomeMaster CLI."""

from __future__ import annotations

import ast
import importlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from homemaster.embedding_client import BGEEmbeddingClient, EmbeddingClientError
from homemaster.llm_client import LLMClientError, RawJsonLLMClient
from homemaster.memory_rag import DEFAULT_EMBEDDING_PROVIDER_NAME
from homemaster.runtime import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_PROVIDER_NAME,
    GENERIC_CONFIG_PATH,
    LEGACY_CONFIG_PATH,
    REPO_ROOT,
    RuntimeConfigError,
    load_provider_config,
)
from homemaster.runtime_memory_store import RuntimeMemoryStore

DoctorStatus = Literal["PASS", "WARN", "FAIL"]


class DoctorCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: DoctorStatus
    message: str
    impact: str | None = None
    suggestion: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class DoctorReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    live: bool
    config_source: str
    checks: list[DoctorCheck]

    @property
    def has_failures(self) -> bool:
        return any(check.status == "FAIL" for check in self.checks)


def run_doctor(*, live: bool = False) -> DoctorReport:
    """Run local checks, and optional provider smoke checks, without exposing secrets."""

    checks: list[DoctorCheck] = []
    config_source = _config_source()
    checks.append(_python_environment_check())
    checks.extend(_import_checks())
    checks.append(_config_check(config_source))
    checks.append(_embedding_endpoint_check())
    checks.append(_ignored_paths_check())
    checks.append(_runtime_memory_store_check())
    checks.append(_import_boundary_check())
    if live:
        checks.extend(_live_provider_checks())
    return DoctorReport(live=live, config_source=config_source, checks=checks)


def render_doctor_text(report: DoctorReport) -> str:
    lines = ["HomeMaster Doctor", f"config_source: {report.config_source}"]
    for check in report.checks:
        lines.append(f"{check.status:<4} {check.name}: {check.message}")
        if check.suggestion:
            lines.append(f"     suggestion: {check.suggestion}")
    return "\n".join(lines)


def _python_environment_check() -> DoctorCheck:
    executable = Path(sys.executable)
    status: DoctorStatus = "PASS" if ".venv" in executable.parts else "WARN"
    return DoctorCheck(
        name="python_environment",
        status=status,
        message=f"python={executable}",
        suggestion="Use /Users/wylam/Documents/workspace/HomeMaster/.venv/bin/python"
        if status == "WARN"
        else None,
        details={"executable": str(executable)},
    )


def _import_checks() -> list[DoctorCheck]:
    modules = ["homemaster", "pydantic", "httpx", "typer", "bm25s", "jieba"]
    checks: list[DoctorCheck] = []
    for module in modules:
        try:
            importlib.import_module(module)
        except Exception as exc:  # pragma: no cover - exact import failure is environment-specific
            checks.append(
                DoctorCheck(
                    name=f"import:{module}",
                    status="FAIL",
                    message=f"cannot import {module}: {type(exc).__name__}",
                    suggestion="Install project dependencies into .venv.",
                )
            )
        else:
            checks.append(
                DoctorCheck(name=f"import:{module}", status="PASS", message="import ok")
            )
    return checks


def _config_source() -> str:
    if GENERIC_CONFIG_PATH.exists():
        return str(GENERIC_CONFIG_PATH.relative_to(REPO_ROOT))
    if LEGACY_CONFIG_PATH.exists():
        return f"legacy fallback: {LEGACY_CONFIG_PATH.relative_to(REPO_ROOT)}"
    return str(DEFAULT_CONFIG_PATH.relative_to(REPO_ROOT))


def _config_check(config_source: str) -> DoctorCheck:
    try:
        chat_provider = load_provider_config(
            DEFAULT_CONFIG_PATH,
            provider_name=DEFAULT_PROVIDER_NAME,
        )
        embedding_provider = load_provider_config(
            DEFAULT_CONFIG_PATH,
            provider_name=DEFAULT_EMBEDDING_PROVIDER_NAME,
        )
    except RuntimeConfigError as exc:
        return DoctorCheck(
            name="config_source",
            status="FAIL",
            message=str(exc),
            impact="provider config is required for live LLM and embedding checks",
            suggestion="Create config/api_config.json or keep the legacy fallback locally ignored.",
            details={"config_source": config_source},
        )
    return DoctorCheck(
        name="config_source",
        status="PASS",
        message="provider config loaded",
        details={
            "config_source": config_source,
            "chat_provider": chat_provider.public_summary(),
            "embedding_provider": embedding_provider.public_summary(),
        },
    )


def _embedding_endpoint_check() -> DoctorCheck:
    try:
        provider = load_provider_config(
            DEFAULT_CONFIG_PATH,
            provider_name=DEFAULT_EMBEDDING_PROVIDER_NAME,
        )
    except RuntimeConfigError as exc:
        return DoctorCheck(
            name="embedding_endpoint",
            status="FAIL",
            message=str(exc),
            suggestion="Add a MemoryEmbedding provider with an embeddings endpoint.",
        )
    client = BGEEmbeddingClient(provider)
    try:
        endpoint = client.public_summary()["endpoint"]
    finally:
        client.close()
    status: DoctorStatus = "PASS" if str(endpoint).endswith("/v1/embeddings") else "WARN"
    return DoctorCheck(
        name="embedding_endpoint",
        status=status,
        message=f"embedding endpoint={endpoint}",
        suggestion="Use /v1/embeddings for BGE-M3, not /v1/messages."
        if status == "WARN"
        else None,
        details={"provider_name": provider.name, "model": provider.model, "endpoint": endpoint},
    )


def _ignored_paths_check() -> DoctorCheck:
    paths = [
        ".cache/homemaster/embeddings/example.json",
        "plan/V1.2/test_results/stage_07/example.log",
        "var/homemaster/memory/example.json",
        "var/homemaster/runs/example/memory/object_memory.json",
    ]
    missed = [path for path in paths if not _git_check_ignore(path)]
    return DoctorCheck(
        name="ignored_runtime_paths",
        status="PASS" if not missed else "FAIL",
        message=(
            "runtime/debug paths are ignored"
            if not missed
            else "some runtime paths are tracked-risk"
        ),
        suggestion="Add missing runtime paths to .gitignore." if missed else None,
        details={"missed": missed},
    )


def _git_check_ignore(path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=REPO_ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _runtime_memory_store_check() -> DoctorCheck:
    base_memory_path = REPO_ROOT / "data" / "scenarios" / "fetch_cup_retry" / "memory.json"
    try:
        with tempfile.TemporaryDirectory(prefix="homemaster-doctor-") as tmp:
            store = RuntimeMemoryStore(Path(tmp) / "memory")
            payload = store.load_runtime_or_base(base_memory_path)
            ok = isinstance(payload.get("object_memory"), list)
    except Exception as exc:  # pragma: no cover - environment failure
        return DoctorCheck(
            name="runtime_memory_store",
            status="FAIL",
            message=f"runtime memory check failed: {type(exc).__name__}",
            suggestion="Check scenario memory fixtures and writable temp directories.",
        )
    return DoctorCheck(
        name="runtime_memory_store",
        status="PASS" if ok else "FAIL",
        message="runtime object memory overlay can read base memory"
        if ok
        else "base memory payload missing object_memory",
    )


def _import_boundary_check() -> DoctorCheck:
    root = REPO_ROOT / "src" / "homemaster"
    offenders: list[str] = []
    for path in sorted(root.glob("**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "task_brain" or alias.name.startswith("task_brain."):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "task_brain" or module.startswith("task_brain."):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{module}")
    return DoctorCheck(
        name="import_boundary",
        status="PASS" if not offenders else "FAIL",
        message="src/homemaster does not import task_brain"
        if not offenders
        else "src/homemaster imports legacy task_brain",
        details={"offenders": offenders},
    )


def _live_provider_checks() -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    checks.append(_live_mimo_smoke())
    checks.append(_live_embedding_smoke())
    return checks


def _live_mimo_smoke() -> DoctorCheck:
    try:
        provider = load_provider_config(DEFAULT_CONFIG_PATH, provider_name=DEFAULT_PROVIDER_NAME)
        client = RawJsonLLMClient(provider)
        try:
            response = client.complete_json(
                '只输出 JSON object: {"ok": true}',
                max_tokens=64,
                temperature=0.0,
            )
        finally:
            client.close()
    except (RuntimeConfigError, LLMClientError) as exc:
        return DoctorCheck(
            name="live_mimo_smoke",
            status="FAIL",
            message=str(exc),
            suggestion="Check provider/auth/network/schema for the chat LLM.",
        )
    return DoctorCheck(
        name="live_mimo_smoke",
        status="PASS" if response.json_payload.get("ok") is True else "WARN",
        message="Mimo returned parseable JSON",
        details={"provider": response.public_summary()},
    )


def _live_embedding_smoke() -> DoctorCheck:
    try:
        provider = load_provider_config(
            DEFAULT_CONFIG_PATH,
            provider_name=DEFAULT_EMBEDDING_PROVIDER_NAME,
        )
        client = BGEEmbeddingClient(provider)
        try:
            response = client.embed_texts(["HomeMaster embedding smoke"])
        finally:
            client.close()
    except (RuntimeConfigError, EmbeddingClientError) as exc:
        return DoctorCheck(
            name="live_embedding_smoke",
            status="FAIL",
            message=str(exc),
            suggestion="Check provider/auth/network/schema for the embedding provider.",
        )
    return DoctorCheck(
        name="live_embedding_smoke",
        status="PASS" if response.embeddings and response.embeddings[0] else "WARN",
        message="BGE-M3 returned an embedding vector",
        details={"provider": response.public_summary()},
    )


def doctor_report_to_json(report: DoctorReport) -> str:
    return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
