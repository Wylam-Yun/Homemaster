"""V2.1 memory configuration and dependency contract tests."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from homemaster.config import HomeMasterConfig, ProviderProfileConfig
from homemaster.config.config import _homemaster_home

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

    # B1: the dependency-free files tier is the default; the full MindMemOS
    # tier must be selected explicitly with ``memory.mode: full``.
    assert config.memory.mode == "files"
    assert config.memory.enabled is False
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
    assert config.memory.dreaming_memory_threshold == 8
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


def test_memory_config_rejects_non_positive_dreaming_threshold() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        HomeMasterConfig(memory={"dreaming_memory_threshold": 0})


def test_enabled_memory_requires_named_embedding_provider_with_embedding_kind() -> None:
    # The embedding-provider binding is only required on the full tier, so the
    # config must select it explicitly now that "files" is the default mode.
    with pytest.raises(ValidationError, match="MemoryEmbedding.*embedding"):
        HomeMasterConfig(
            memory={"mode": "full"},
            providers={"items": [_embedding_provider(kind="chat")]},
        )


def test_files_mode_does_not_require_embedding_provider_or_neo4j_binding() -> None:
    config = HomeMasterConfig(
        memory={"mode": "files"},
        providers={"items": [_embedding_provider(kind="chat")]},
    )

    assert config.memory.mode == "files"
    assert config.memory.enabled is False


@pytest.mark.parametrize(
    ("enabled", "expected_mode"),
    [(True, "full"), (False, "files")],
)
def test_legacy_enabled_maps_to_mode_with_deprecation_warning(
    enabled: bool, expected_mode: str
) -> None:
    with pytest.warns(DeprecationWarning, match="memory.enabled is deprecated"):
        config = HomeMasterConfig(memory={"enabled": enabled})

    assert config.memory.mode == expected_mode
    # The stored enabled flag is a projected view of the selected tier.
    assert config.memory.enabled is (expected_mode == "full")


def test_memory_mode_and_legacy_enabled_combination_matrix(
    recwarn: pytest.WarningsRecorder,
) -> None:
    # A plain mode key never warns.
    assert HomeMasterConfig(memory={"mode": "files"}).memory.mode == "files"
    assert not [
        warning
        for warning in recwarn.list
        if issubclass(warning.category, DeprecationWarning)
    ]
    # A consistent enabled+mode pair still warns once and resolves to files.
    with pytest.warns(DeprecationWarning):
        consistent = HomeMasterConfig(memory={"mode": "files", "enabled": False})
    assert consistent.memory.mode == "files"
    with pytest.warns(DeprecationWarning):
        full = HomeMasterConfig(memory={"mode": "full", "enabled": True})
    assert full.memory.mode == "full"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"enabled": False, "mode": "full"},
            "memory.enabled.*conflicts with memory.mode",
        ),
        (
            {"enabled": True, "mode": "files"},
            "memory.enabled.*conflicts with memory.mode",
        ),
        ({"enabled": "yes-i-guess"}, "memory.enabled must be a boolean"),
        ({"mode": "bogus"}, "Input should be 'files' or 'full'"),
    ],
)
def test_memory_mode_rejects_invalid_combinations(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        HomeMasterConfig(memory=payload)


def test_memory_config_captures_legacy_file_path_only_as_migration_input(tmp_path: Path) -> None:
    config = HomeMasterConfig(memory={"root": tmp_path / "old-files"})

    # The default data root is anchored at $HOMEMASTER_HOME (~/.homemaster by
    # default), not a hardcoded literal.
    assert config.memory.data_root == _homemaster_home(os.environ) / "memory"
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


def test_memory_qdrant_defaults_to_embedded() -> None:
    config = HomeMasterConfig(providers={"items": [_embedding_provider()]})
    assert config.memory.qdrant.mode == "embedded"
    assert config.memory.qdrant.url == "http://127.0.0.1:6333"


def test_memory_qdrant_server_mode_accepts_connection_fields() -> None:
    config = HomeMasterConfig(
        memory={
            "qdrant": {
                "mode": "server",
                "url": "http://qdrant.internal:6333",
                "api_key": "private-qdrant-key",
                "prefer_grpc": True,
                "grpc_port": 6400,
            }
        }
    )
    assert config.memory.qdrant.mode == "server"
    assert config.memory.qdrant.url == "http://qdrant.internal:6333"
    assert config.memory.qdrant.api_key.get_secret_value() == "private-qdrant-key"
    assert "private-qdrant-key" not in repr(config.memory.qdrant)
    assert config.memory.qdrant.prefer_grpc is True
    assert config.memory.qdrant.grpc_port == 6400


def test_memory_qdrant_managed_local_fields(tmp_path: Path) -> None:
    home = tmp_path / "qdrant-home"
    home.mkdir()
    config = HomeMasterConfig(
        memory={
            "qdrant": {
                "mode": "managed_local",
                "home": home,
                "rest_port": 16333,
                "grpc_port": 16334,
            }
        }
    )
    assert config.memory.qdrant.mode == "managed_local"
    assert config.memory.qdrant.home == home
    assert config.memory.qdrant.effective_url == "http://127.0.0.1:16333"
    assert config.memory.mindmemos_qdrant_service_root == (
        config.memory.data_root / "mindmemos" / "qdrant_service"
    )


def test_memory_qdrant_managed_local_rejects_equal_ports(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="must differ"):
        HomeMasterConfig(
            memory={
                "qdrant": {
                    "mode": "managed_local",
                    "home": tmp_path,
                    "rest_port": 6333,
                    "grpc_port": 6333,
                }
            }
        )


def test_memory_qdrant_rejects_out_of_range_ports() -> None:
    with pytest.raises(ValidationError):
        HomeMasterConfig(
            memory={"qdrant": {"mode": "server", "grpc_port": 70000}}
        )


def test_memory_qdrant_home_is_anchored_to_config_dir(tmp_path: Path) -> None:
    from homemaster.config.config import _anchor_config_relative_paths

    anchored = _anchor_config_relative_paths(
        {"memory": {"qdrant": {"mode": "managed_local", "home": "vendor/qdrant"}}},
        config_dir=tmp_path,
    )
    assert anchored["memory"]["qdrant"]["home"] == (tmp_path / "vendor/qdrant").resolve()


@pytest.mark.parametrize(
    "url",
    ["not-a-url", "qdrant.internal:6333", "bolt://127.0.0.1:6333", ""],
)
def test_memory_qdrant_rejects_invalid_url(url: str) -> None:
    with pytest.raises(ValidationError, match="http"):
        HomeMasterConfig(memory={"qdrant": {"mode": "server", "url": url}})


def test_memory_config_rejects_mixed_new_and_legacy_path_fields(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="cannot be combined"):
        HomeMasterConfig(memory={"data_root": tmp_path / "new", "root": tmp_path / "old"})


def test_project_locks_memory_search_dependencies_in_memory_extra() -> None:
    """W1 tiering: the MindMemOS dependency stack lives in the ``memory`` extra
    so a wheel install without it can still run the files tier."""

    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    memory_extra = project["project"]["optional-dependencies"]["memory"]

    assert all(not item.startswith("mem0ai") for item in dependencies)
    # The lightweight core must not require the full-tier external stack.
    for prefix in (
        "qdrant-client",
        "neo4j",
        "spacy",
        "en-core-web-sm",
        "fastembed",
        "aiokafka",
        "jieba",
        "omegaconf",
        "litellm",
    ):
        assert all(
            not item.startswith(prefix) for item in dependencies
        ), f"{prefix} must not be a core dependency"
    assert "qdrant-client==1.18.0" in memory_extra
    assert "spacy==3.8.14" in memory_extra
    assert (
        "en-core-web-sm @ https://github.com/explosion/spacy-models/releases/download/"
        "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl" in memory_extra
    )


def test_homemaster_home_anchors_default_memory_skill_and_attachment_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """$HOMEMASTER_HOME must re-root every built-in per-user data default."""

    home = tmp_path / "hm-home"
    monkeypatch.setenv("HOMEMASTER_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path / "ignored-home"))

    config = HomeMasterConfig()

    assert config.memory.data_root == home / "memory"
    assert config.memory.files_root == home / "memory" / "files"
    assert config.skills.user_dirs == (home / "skills",)
    assert config.gateway.feishu.attachment_root == home / "attachments" / "feishu"


def test_default_roots_track_injected_home_without_homemaster_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("HOMEMASTER_HOME", raising=False)
    isolated_home = tmp_path / "isolated-home"
    monkeypatch.setenv("HOME", str(isolated_home))

    config = HomeMasterConfig()

    assert config.memory.data_root == isolated_home / ".homemaster" / "memory"
    assert config.skills.user_dirs == (isolated_home / ".homemaster" / "skills",)
    assert config.gateway.feishu.attachment_root == (
        isolated_home / ".homemaster" / "attachments" / "feishu"
    )
