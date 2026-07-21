"""Environment doctor for the HomeMaster CLI."""

from __future__ import annotations

import asyncio
import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from homemaster.config import (
    DEFAULT_EMBEDDING_PROVIDER_NAME,
    DEFAULT_PROVIDER_NAME,
    HOMEMASTER_CONFIG_PATH,
    REPO_ROOT,
    ConfigError,
    load_config,
)
from homemaster.providers.embedding_client import BGEEmbeddingClient, EmbeddingClientError
from homemaster.providers.llm_client import LLMClient, LLMClientError

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
            checks.append(DoctorCheck(name=f"import:{module}", status="PASS", message="import ok"))
    return checks


def _config_source() -> str:
    return str(HOMEMASTER_CONFIG_PATH.relative_to(REPO_ROOT))


def _config_check(config_source: str) -> DoctorCheck:
    try:
        config = load_config(HOMEMASTER_CONFIG_PATH)
        chat_provider = config.get_provider(DEFAULT_PROVIDER_NAME, kind="chat")
        embedding_provider = config.get_provider(
            DEFAULT_EMBEDDING_PROVIDER_NAME,
            kind="embedding",
        )
    except ConfigError as exc:
        return DoctorCheck(
            name="config_source",
            status="FAIL",
            message=str(exc),
            impact="provider config is required for live LLM and embedding checks",
            suggestion="Configure providers in config/homemaster.yaml.",
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
            "field_sources": {
                "default_provider": config.field_source("providers.default"),
                "chat_model": config.field_source(f"providers.{chat_provider.name}.model"),
                "chat_auth": config.field_source(f"providers.{chat_provider.name}.api_keys"),
                "embedding_model": config.field_source(
                    f"providers.{embedding_provider.name}.model"
                ),
                "embedding_auth": config.field_source(
                    f"providers.{embedding_provider.name}.api_keys"
                ),
            },
        },
    )


def _embedding_endpoint_check() -> DoctorCheck:
    try:
        provider = load_config(HOMEMASTER_CONFIG_PATH).get_provider(
            DEFAULT_EMBEDDING_PROVIDER_NAME,
            kind="embedding",
        )
    except ConfigError as exc:
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
        suggestion="Use /v1/embeddings for BGE-M3, not /v1/messages." if status == "WARN" else None,
        details={"provider_name": provider.name, "model": provider.model, "endpoint": endpoint},
    )


def _ignored_paths_check() -> DoctorCheck:
    paths = [
        ".cache/homemaster/embeddings/example.json",
        "var/homemaster/memory/example.json",
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


def _live_provider_checks() -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    checks.append(_live_mimo_smoke())
    checks.append(_live_embedding_smoke())
    return checks


def _live_mimo_smoke() -> DoctorCheck:
    try:
        provider = load_config(HOMEMASTER_CONFIG_PATH).get_provider(
            DEFAULT_PROVIDER_NAME, kind="chat"
        )
        client = LLMClient(provider)

        async def smoke():
            try:
                return await client.complete_json(
                    '只输出 JSON object: {"ok": true}',
                    temperature=0.0,
                )
            finally:
                await client.aclose()

        response = asyncio.run(smoke())
    except (ConfigError, LLMClientError) as exc:
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
        provider = load_config(HOMEMASTER_CONFIG_PATH).get_provider(
            DEFAULT_EMBEDDING_PROVIDER_NAME,
            kind="embedding",
        )
        client = BGEEmbeddingClient(provider)
        try:
            response = client.embed_texts(["HomeMaster embedding smoke"])
        finally:
            client.close()
    except (ConfigError, EmbeddingClientError) as exc:
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
