"""Environment doctor for the HomeMaster CLI."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import select
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
from homemaster.memory.migration import MemoryMigrationCoordinator
from homemaster.providers.embedding_client import BGEEmbeddingClient, EmbeddingClientError
from homemaster.providers.errors import LLMClientError
from homemaster.substrate.as_llm_client import AsLLMClient

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


def run_doctor(*, live: bool = False, alfworld: bool = False) -> DoctorReport:
    """Run local checks and optional provider smoke checks with authoritative details."""

    checks: list[DoctorCheck] = []
    config_source = _config_source()
    memory_mode = _configured_memory_mode()
    checks.append(_python_environment_check())
    checks.append(_memory_mode_check(memory_mode))
    checks.extend(_import_checks(memory_mode=memory_mode))
    if memory_mode == "full":
        # MindMemOS runtime/backend checks only exist on the full memory tier;
        # the files tier is dependency-free and skips them entirely.
        checks.append(_mindmemos_blackbox_check())
    if alfworld:
        checks.append(_alfworld_binding_check())
        checks.append(_worker_protocol_check())
    checks.append(_config_check(config_source, memory_mode=memory_mode))
    if memory_mode == "full":
        checks.append(_embedding_endpoint_check())
        checks.append(_memory_backend_check())
    checks.append(_ignored_paths_check())
    if live:
        checks.extend(_live_provider_checks(memory_mode=memory_mode))
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
    resolved = executable.resolve()
    managed_parts = (executable.parts, resolved.parts)
    managed_runtime = any(
        ".venv" in parts
        or any(
            parts[index : index + 2] == (".runtime", "venv")
            for index in range(len(parts) - 1)
        )
        for parts in managed_parts
    )
    status: DoctorStatus = "PASS" if managed_runtime else "WARN"
    return DoctorCheck(
        name="python_environment",
        status=status,
        message=f"python={executable}",
        suggestion="Use the project-managed .runtime/venv Python executable"
        if status == "WARN"
        else None,
        details={"executable": str(executable)},
    )


# Modules that only the full MindMemOS tier needs.  They ship in the
# ``memory`` optional extra, so the files tier must never require them.
# ``fastembed`` is no longer a dependency at all: MindMemOS sparse vectors
# are self-encoded, so it appears on neither tier's import surface.
_FULL_TIER_IMPORT_MODULES = frozenset(
    {"jieba", "mindmemos", "qdrant_client", "neo4j", "spacy"}
)


def _configured_memory_mode() -> str:
    """Best-effort read of ``memory.mode``; fail closed to the full tier."""

    try:
        mode = load_config(HOMEMASTER_CONFIG_PATH).memory.mode
    except Exception:
        return "full"
    return mode if mode in ("files", "full") else "full"


def _memory_mode_check(memory_mode: str) -> DoctorCheck:
    suffix = (
        "MindMemOS full-tier checks skipped"
        if memory_mode == "files"
        else "full MindMemOS tier"
    )
    return DoctorCheck(
        name="memory_mode",
        status="PASS",
        message=f"memory.mode={memory_mode} ({suffix})",
        details={"memory_mode": memory_mode},
    )


def _import_checks(*, memory_mode: str = "full") -> list[DoctorCheck]:
    modules = [
        "homemaster",
        "pydantic",
        "httpx",
        "typer",
        "jieba",
        "mindmemos",
        "qdrant_client",
    ]
    if memory_mode == "files":
        modules = [name for name in modules if name not in _FULL_TIER_IMPORT_MODULES]
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


def _mindmemos_blackbox_check() -> DoctorCheck:
    """Prove the installed MindMemOS package and a real writable runtime root."""

    try:
        module = importlib.import_module("mindmemos")
        origin = Path(getattr(module, "__file__", "")).resolve()
        third_party = (REPO_ROOT / "third_party" / "MindMemOS").resolve()
        if third_party in origin.parents:
            return DoctorCheck(
                name="mindmemos_runtime",
                status="FAIL",
                message="mindmemos resolves to the repository source tree",
                suggestion="Install the locked local workspace dependency into .runtime/venv.",
                details={"origin": str(origin)},
            )
        root = REPO_ROOT / ".runtime" / "doctor" / "mindmemos"
        root.mkdir(parents=True, exist_ok=True)
        marker = root / "blackbox.json"
        payload = {"schema": "homemaster-mindmemos-doctor-v1", "pid": os.getpid()}
        marker.write_text(json.dumps(payload), encoding="utf-8")
        observed = json.loads(marker.read_text(encoding="utf-8"))
        if observed != payload:
            raise RuntimeError("MindMemOS runtime readback did not match the written state")
    except Exception as exc:  # pragma: no cover - environment-specific
        return DoctorCheck(
            name="mindmemos_runtime",
            status="FAIL",
            message=f"MindMemOS import/read-write blackbox failed: {type(exc).__name__}",
            suggestion="Run scripts/setup.sh in the project-managed runtime.",
            details={"error": str(exc)},
        )
    return DoctorCheck(
        name="mindmemos_runtime",
        status="PASS",
        message="MindMemOS import and runtime-root read/write blackbox passed",
        details={"origin": str(origin), "marker": str(marker)},
    )


def _alfworld_binding_check() -> DoctorCheck:
    binding = REPO_ROOT / ".runtime" / "alfworld-binding.json"
    if not binding.is_file():
        return DoctorCheck(
            name="alfworld_binding",
            status="FAIL",
            message="ALFWorld runtime binding is missing",
            suggestion="Run scripts/setup-alfworld.sh --root <alfworld checkout>.",
            details={"binding": str(binding)},
        )
    try:
        payload = json.loads(binding.read_text(encoding="utf-8"))
        python = Path(payload["python"]).resolve(strict=True)
        root = Path(payload["root"]).resolve(strict=True)
        if not (root / "configs" / "base_config.yaml").is_file():
            raise RuntimeError("configs/base_config.yaml is missing")
        if not (root / "data" / "json_2.1.1").is_dir():
            raise RuntimeError("data/json_2.1.1 is missing")
        probe = subprocess.run(
            [str(python), "-c", "import alfworld, ai2thor, torch, cv2, PIL, numpy"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode:
            raise RuntimeError((probe.stderr or probe.stdout).strip()[-500:])
    except Exception as exc:  # pragma: no cover - environment-specific
        return DoctorCheck(
            name="alfworld_binding",
            status="FAIL",
            message=f"ALFWorld binding/import check failed: {type(exc).__name__}",
        suggestion=(
            "Recreate the isolated worker environment from "
            "config/alfworld/requirements.lock."
        ),
            details={"binding": str(binding), "error": str(exc)},
        )
    return DoctorCheck(
        name="alfworld_binding",
        status="PASS",
        message="ALFWorld worker imports and asset binding passed",
        details={"binding": str(binding), "python": str(python), "root": str(root)},
    )


def _worker_protocol_check() -> DoctorCheck:
    worker = REPO_ROOT / "workers" / "alfworld_worker" / "main.py"
    if not worker.is_file():
        return DoctorCheck(
            name="alfworld_worker_ipc",
            status="FAIL",
            message="isolated worker entrypoint is missing",
            suggestion="Restore workers/alfworld_worker/main.py.",
        )
    binding = REPO_ROOT / ".runtime" / "alfworld-binding.json"
    try:
        payload = json.loads(binding.read_text(encoding="utf-8"))
        python = Path(payload["python"]).resolve(strict=True)
    except Exception as exc:
        return DoctorCheck(
            name="alfworld_worker_ipc",
            status="FAIL",
            message="worker IPC probe cannot resolve the ALFWorld Python binding",
            details={"error": str(exc)},
        )
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    process = subprocess.Popen(
        [str(python), str(worker)],
        cwd=str(worker.parent),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None and process.stdin is not None
        ready = _read_worker_line(process.stdout, timeout_s=5.0)
        if ready.get("status") != "ready" or ready.get("external_return_code") != 0:
            raise RuntimeError(f"unexpected ready response: {ready}")
        request_id = "doctor-close"
        process.stdin.write(
            json.dumps(
                {
                    "protocol": "homemaster-alfworld-v1",
                    "request_id": request_id,
                    "operation": "close",
                    "payload": {},
                }
            )
            + "\n"
        )
        process.stdin.flush()
        closed = _read_worker_line(process.stdout, timeout_s=5.0)
        if closed.get("request_id") != request_id or closed.get("external_return_code") != 0:
            raise RuntimeError(f"unexpected close response: {closed}")
        return_code = process.wait(timeout=5)
        if return_code != 0:
            raise RuntimeError(f"worker exited with {return_code}")
    except Exception as exc:  # pragma: no cover - environment-specific
        process.kill()
        process.wait(timeout=5)
        return DoctorCheck(
            name="alfworld_worker_ipc",
            status="FAIL",
            message=f"isolated reset/close protocol probe failed: {type(exc).__name__}",
            suggestion="Run the worker directly and inspect its stderr before live THOR tests.",
            details={"error": str(exc)},
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    return DoctorCheck(
        name="alfworld_worker_ipc",
        status="PASS",
        message="isolated worker ready/close protocol and process cleanup passed",
    )


def _read_worker_line(stream: Any, *, timeout_s: float) -> dict[str, Any]:
    ready, _, _ = select.select([stream], [], [], timeout_s)
    if not ready:
        raise TimeoutError("worker response timed out")
    line = stream.readline()
    value = json.loads(line)
    if not isinstance(value, dict):
        raise RuntimeError("worker response is not an object")
    return value


def _config_source() -> str:
    try:
        return str(HOMEMASTER_CONFIG_PATH.relative_to(REPO_ROOT))
    except ValueError:
        return str(HOMEMASTER_CONFIG_PATH)


def _config_check(config_source: str, *, memory_mode: str = "full") -> DoctorCheck:
    try:
        config = load_config(HOMEMASTER_CONFIG_PATH)
        chat_provider = config.get_provider(DEFAULT_PROVIDER_NAME, kind="chat")
        # The embedding provider is only a hard requirement on the full tier.
        embedding_provider = None
        try:
            embedding_provider = config.get_provider(
                DEFAULT_EMBEDDING_PROVIDER_NAME,
                kind="embedding",
            )
        except ConfigError:
            if memory_mode == "full":
                raise
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
            "memory_mode": memory_mode,
            "chat_provider": chat_provider.public_summary(),
            "embedding_provider": (
                embedding_provider.public_summary()
                if embedding_provider is not None
                else None
            ),
            "field_sources": {
                "default_provider": config.field_source("providers.default"),
                "chat_model": config.field_source(f"providers.{chat_provider.name}.model"),
                "chat_auth": config.field_source(f"providers.{chat_provider.name}.api_keys"),
                "embedding_model": (
                    config.field_source(f"providers.{embedding_provider.name}.model")
                    if embedding_provider is not None
                    else None
                ),
                "embedding_auth": (
                    config.field_source(f"providers.{embedding_provider.name}.api_keys")
                    if embedding_provider is not None
                    else None
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
        suggestion="Use the provider's exact /v1/embeddings path." if status == "WARN" else None,
        details={"provider_name": provider.name, "model": provider.model, "endpoint": endpoint},
    )


def _memory_backend_check() -> DoctorCheck:
    try:
        config = load_config(HOMEMASTER_CONFIG_PATH)
    except ConfigError as exc:
        return DoctorCheck(
            name="memory_backend",
            status="FAIL",
            message=str(exc),
            impact="five structured memory tools are unavailable; file memory remains independent",
            suggestion="Fix the memory and MemoryEmbedding configuration.",
        )
    if config.memory.mode != "full" or not config.memory.enabled:
        return DoctorCheck(
            name="memory_backend",
            status="WARN",
            message=(
                f"memory.mode={config.memory.mode!r} does not compose the MindMemOS backend"
            ),
            impact=(
                "the six mindmemos_* memory tools are unavailable; "
                "file memory remains independent"
            ),
            suggestion="Set memory.mode to full to use the MindMemOS backend.",
            details={"memory_mode": config.memory.mode, "enabled": config.memory.enabled},
        )
    migration = MemoryMigrationCoordinator(config.memory).inspect()
    if migration.status != "ready":
        return DoctorCheck(
            name="memory_backend",
            status="FAIL" if migration.status == "conflict" else "WARN",
            message=(
                f"migration_conflict: {migration.reason}"
                if migration.status == "conflict"
                else f"migration_required: {migration.reason}"
            ),
            impact="memory stores remain unopened until migration completes",
            suggestion="Run `homemaster memory migrate --config <path>`.",
            details={
                "enabled": True,
                "migration_status": migration.status,
                "data_root": str(migration.data_root),
                "legacy_fields": list(migration.legacy_fields),
                **_neo4j_details(config),
            },
        )
    return DoctorCheck(
        name="memory_backend",
        status="PASS",
        message="memory configuration and migration state are ready; backend was not opened",
        details={
            "enabled": True,
            "probe": "not_opened",
            "embedding_provider_name": config.memory.embedding_provider_name,
            "qdrant_path": str(config.memory.mindmemos_qdrant_path),
            "data_root": str(config.memory.data_root),
            **_neo4j_details(config),
        },
    )


def _neo4j_details(config: Any) -> dict[str, Any]:
    neo4j = config.memory.neo4j
    details: dict[str, Any] = {
        "neo4j_mode": neo4j.mode,
        "neo4j_uri": neo4j.uri,
        "neo4j_username": neo4j.username,
        "neo4j_database": neo4j.database,
    }
    if neo4j.mode == "managed_local":
        details.update(
            {
                "neo4j_home": str(neo4j.home),
                "java_home": str(neo4j.java_home),
            }
        )
    return details


def _ignored_paths_check() -> DoctorCheck:
    if not _is_git_checkout():
        return DoctorCheck(
            name="ignored_runtime_paths",
            status="WARN",
            message="gitignore check is not applicable outside a Git checkout",
            details={"missed": [], "not_applicable": True},
        )
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


def _is_git_checkout() -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _git_check_ignore(path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=REPO_ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _live_provider_checks(*, memory_mode: str = "full") -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    checks.append(_live_mimo_smoke())
    if memory_mode == "full":
        checks.append(_live_embedding_smoke())
    return checks


def _live_mimo_smoke() -> DoctorCheck:
    try:
        provider = load_config(HOMEMASTER_CONFIG_PATH).get_provider(
            DEFAULT_PROVIDER_NAME, kind="chat"
        )
        client = AsLLMClient(provider)

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
        message="MemoryEmbedding returned an embedding vector",
        details={"provider": response.public_summary()},
    )


def doctor_report_to_json(report: DoctorReport) -> str:
    return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
