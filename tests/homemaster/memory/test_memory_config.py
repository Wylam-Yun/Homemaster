"""V2.1 memory configuration and dependency contract tests."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from homemaster.config import HomeMasterConfig, ProviderProfileConfig

REPO_ROOT = Path(__file__).resolve().parents[3]


def _embedding_provider(*, kind: str = "embedding") -> ProviderProfileConfig:
    return ProviderProfileConfig(
        name="MemoryEmbedding",
        api_format="openai",
        base_url="https://embedding.example/v1",
        embedding_url="https://embedding.example/v1/embeddings",
        model="Qwen/Qwen3-Embedding-8B",
        api_keys=["test-key"],
        kind=kind,
    )


def test_memory_config_defaults_are_single_backend_and_expand_private_paths() -> None:
    config = HomeMasterConfig(providers={"items": [_embedding_provider()]})

    assert config.memory.enabled is True
    assert config.memory.data_root.is_absolute()
    assert config.memory.root.is_absolute()
    assert config.memory.root == config.memory.data_root / "files"
    assert config.memory.soul_path == config.memory.root / "SOUL.md"
    assert config.memory.user_path == config.memory.root / "USER.md"
    assert config.memory.memory_path == config.memory.root / "MEMORY.md"
    assert config.memory.user_char_limit == 1375
    assert config.memory.memory_char_limit == 2200
    assert config.memory.embedding_provider_name == "MemoryEmbedding"
    assert config.memory.embedding_dimensions == 4096
    assert config.memory.mindmemos_qdrant_path == (
        config.memory.data_root / "mindmemos" / "qdrant"
    )
    assert config.memory.evidence_db_path == config.memory.data_root / "evidence.sqlite3"
    assert not hasattr(config.memory, "mem0")
    assert not hasattr(config.memory, "backend")


def test_memory_config_accepts_private_managed_neo4j_credentials(tmp_path: Path) -> None:
    neo4j_home = tmp_path / "neo4j-home"
    java_home = tmp_path / "java-home"
    config = HomeMasterConfig(
        memory={
            "data_root": tmp_path / "memory",
            "neo4j": {
                "mode": "managed_local",
                "home": neo4j_home,
                "java_home": java_home,
                "uri": "bolt://127.0.0.1:7687",
                "username": "neo4j",
                "password": "private-test-password",
                "database": "neo4j",
                "start_timeout_seconds": 12,
                "stop_timeout_seconds": 7,
            },
        }
    )

    assert config.memory.neo4j.mode == "managed_local"
    assert config.memory.neo4j.home == neo4j_home
    assert config.memory.neo4j.java_home == java_home
    assert config.memory.neo4j.password.get_secret_value() == "private-test-password"
    assert "private-test-password" not in repr(config.memory.neo4j)
    assert config.memory.neo4j_runtime_root == (
        tmp_path / "memory" / "mindmemos" / "neo4j" / "runtime"
    )


def test_managed_neo4j_requires_home_java_and_password() -> None:
    with pytest.raises(ValidationError, match="managed_local.*home.*java_home.*password"):
        HomeMasterConfig(memory={"neo4j": {"mode": "managed_local"}})


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"user_char_limit": 0}, "greater than 0"),
        ({"memory_char_limit": 0}, "greater than 0"),
        ({"embedding_dimensions": 0}, "greater than 0"),
        ({"soul_file": "../SOUL.md"}, "plain file name"),
    ],
)
def test_memory_config_rejects_invalid_values(payload: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        HomeMasterConfig(memory=payload)


def test_enabled_memory_requires_named_embedding_provider_with_embedding_kind() -> None:
    with pytest.raises(ValidationError, match="MemoryEmbedding.*embedding"):
        HomeMasterConfig(providers={"items": [_embedding_provider(kind="chat")]})


def test_disabled_memory_does_not_require_embedding_provider() -> None:
    config = HomeMasterConfig(memory={"enabled": False})

    assert config.memory.enabled is False


def test_memory_config_captures_legacy_file_path_only_as_migration_input(tmp_path: Path) -> None:
    config = HomeMasterConfig(memory={"root": tmp_path / "old-files"})

    assert config.memory.data_root == Path("~/.homemaster/memory").expanduser()
    assert config.memory.migration_spec.files_source == tmp_path / "old-files"
    assert config.memory.migration_spec.explicit_legacy_fields == ("memory.root",)
    assert "root" not in config.memory.model_fields_set
    assert not hasattr(config.memory, "mem0")


def test_explicit_data_root_does_not_probe_global_legacy_file_memory(tmp_path: Path) -> None:
    config = HomeMasterConfig(memory={"data_root": tmp_path / "memory"})

    assert config.memory.migration_spec.files_source == tmp_path / "memory" / "files"


def test_memory_config_rejects_removed_mem0_settings() -> None:
    with pytest.raises(ValidationError, match="mem0"):
        HomeMasterConfig(memory={"mem0": {"qdrant_path": "/tmp/old-qdrant"}})


def test_memory_config_rejects_mixed_new_and_legacy_path_fields(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="cannot be combined"):
        HomeMasterConfig(memory={"data_root": tmp_path / "new", "root": tmp_path / "old"})


def test_project_locks_memory_search_dependencies_and_spacy_model() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]

    assert all(not item.startswith("mem0ai") for item in dependencies)
    assert "qdrant-client==1.18.0" in dependencies
    assert "spacy==3.8.14" in dependencies
    assert (
        "en-core-web-sm @ https://github.com/explosion/spacy-models/releases/download/"
        "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl" in dependencies
    )
    fastembed = [value for value in dependencies if value.startswith("fastembed==")]
    assert len(fastembed) == 1
