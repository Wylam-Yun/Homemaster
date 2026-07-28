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
    assert config.memory.root.is_absolute()
    assert config.memory.soul_path == config.memory.root / "SOUL.md"
    assert config.memory.user_path == config.memory.root / "USER.md"
    assert config.memory.memory_path == config.memory.root / "MEMORY.md"
    assert config.memory.user_char_limit == 1375
    assert config.memory.memory_char_limit == 2200
    assert config.memory.embedding_provider_name == "MemoryEmbedding"
    assert config.memory.mem0.qdrant_path.is_absolute()
    assert config.memory.mem0.history_db_path.is_absolute()
    assert config.memory.mem0.embedding_dimensions == 4096
    assert config.memory.mem0.search_limit == 5
    assert config.memory.mem0.search_threshold == pytest.approx(0.1)
    assert not hasattr(config.memory, "backend")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"user_char_limit": 0}, "greater than 0"),
        ({"memory_char_limit": 0}, "greater than 0"),
        ({"mem0": {"embedding_dimensions": 0}}, "greater than 0"),
        ({"mem0": {"search_limit": 0}}, "greater than or equal to 1"),
        ({"mem0": {"search_limit": 21}}, "less than or equal to 20"),
        ({"mem0": {"search_threshold": -0.01}}, "greater than or equal to 0"),
        ({"mem0": {"search_threshold": 1.01}}, "less than or equal to 1"),
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


def test_project_locks_minimum_memory_dependencies_without_extras() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]

    assert "mem0ai==2.0.13" in dependencies
    fastembed = [value for value in dependencies if value.startswith("fastembed==")]
    assert len(fastembed) == 1
    assert not any(value.startswith("mem0ai[") for value in dependencies)
