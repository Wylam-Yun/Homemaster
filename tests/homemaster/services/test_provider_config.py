"""Tests for ``ProviderConfigService`` — provider CRUD + hot apply (P0-2)."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
import yaml

import homemaster.services.provider_config as provider_config
from homemaster.config.config import HomeMasterConfig
from homemaster.providers.errors import (
    LLMAuthError,
    LLMNetworkError,
    LLMProviderError,
)
from homemaster.services.provider_config import (
    ProviderConfigError,
    ProviderConfigService,
    ProviderConflictError,
    ProviderNotFoundError,
    ProviderValidationError,
    provider_templates,
)


def _runtime_config(path: Path, payload: dict | None = None) -> HomeMasterConfig:
    config = HomeMasterConfig.model_validate(payload or {})
    config._config_path = path
    return config


def _service(
    tmp_path: Path,
    *,
    payload: dict | None = None,
    probe=None,
) -> tuple[ProviderConfigService, HomeMasterConfig, Path]:
    """Service + runtime bound to ``tmp_path/config.yaml``.

    When ``payload`` is given the file is seeded with it and the runtime
    config is validated from the same document — mirroring ``load_config``.
    """

    path = tmp_path / "config.yaml"
    config = _runtime_config(path, payload)
    if payload is not None:
        _seed(path, payload)
    service = ProviderConfigService(config_path=path, runtime_config=config, probe=probe)
    return service, config, path


def _seed(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def _provider_payload(**overrides) -> dict:
    payload = {
        "name": "DeepSeek",
        "api_format": "openai",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key": "sk-deepseek-testkey-0001",
    }
    payload.update(overrides)
    return payload


def _names(path: Path) -> list[str]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [item["name"] for item in raw["providers"]["items"]]


# ---------------------------------------------------------------------------
# upsert → persistence + hot apply
# ---------------------------------------------------------------------------


def test_upsert_persists_yaml_with_600_and_applies_to_runtime(tmp_path: Path) -> None:
    service, runtime, path = _service(tmp_path)

    result = service.upsert(_provider_payload())

    assert result["status"] == "created"
    assert result["default"] == "DeepSeek"
    assert result["provider"]["name"] == "DeepSeek"
    assert result["provider"]["api_key_configured"] is True
    assert result["provider"]["api_key_count"] == 1
    # Key material never leaves the write path.
    assert "sk-deepseek-testkey-0001" not in str(result)

    # The file exists with owner-only permissions and parses through the loader.
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    (item,) = raw["providers"]["items"]
    assert item["api_keys"] == ["sk-deepseek-testkey-0001"]
    assert raw["providers"]["default"] == "DeepSeek"
    assert raw["runtime_defaults"]["default_provider_name"] == "DeepSeek"

    # The runtime object sees the new provider in place — the hot-apply seam
    # _resolve_chat_provider/_provider_selection_error read on the next run.
    assert runtime.providers.default == "DeepSeek"
    provider = runtime.get_provider("DeepSeek", kind="chat")
    assert provider.api_keys == ("sk-deepseek-testkey-0001",)
    assert provider.model == "deepseek-chat"


def test_upsert_leaves_no_tmp_files_and_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _runtime, path = _service(tmp_path)
    service.upsert(_provider_payload())
    assert list(tmp_path.glob("*.tmp")) == []

    # A failure inside os.replace must keep the previous file and clean up.
    original = path.read_text(encoding="utf-8")

    def _boom(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(provider_config.os, "replace", _boom)
    with pytest.raises(ProviderConfigError):
        service.upsert(_provider_payload(name="Other", model="m2"))
    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_upsert_template_maps_template_fields(tmp_path: Path) -> None:
    service, _runtime, path = _service(tmp_path)

    result = service.upsert({"name": "ds", "template": "deepseek", "api_key": "sk-tpl-123456"})

    assert result["provider"]["api_format"] == "deepseek"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    (item,) = raw["providers"]["items"]
    assert item["api_format"] == "deepseek"
    # ``transport`` is deprecated and no longer written into items.
    assert "transport" not in item
    assert item["base_url"] == "https://api.deepseek.com"
    assert item["model"] == "deepseek-chat"


def test_upsert_conflict_requires_overwrite(tmp_path: Path) -> None:
    service, _runtime, _path = _service(tmp_path)
    service.upsert(_provider_payload())

    identical = service.upsert(_provider_payload())
    assert identical["status"] == "unchanged"

    with pytest.raises(ProviderConflictError):
        service.upsert(_provider_payload(model="deepseek-reasoner"))

    replaced = service.upsert(_provider_payload(model="deepseek-reasoner"), overwrite=True)
    assert replaced["status"] == "updated"
    assert replaced["provider"]["model"] == "deepseek-reasoner"


def test_upsert_validation_failure_leaves_file_untouched(tmp_path: Path) -> None:
    service, runtime, path = _service(tmp_path)
    service.upsert(_provider_payload())
    before = path.read_text(encoding="utf-8")

    with pytest.raises(ProviderValidationError):
        service.upsert(_provider_payload(name="Bad", api_format="bogus-format"))
    with pytest.raises(ProviderValidationError):
        service.upsert(_provider_payload(name="Bad", rogue_field="nope"))
    with pytest.raises(ProviderValidationError):
        service.upsert({"template": "deepseek"})  # missing name

    assert path.read_text(encoding="utf-8") == before
    assert _names(path) == ["DeepSeek"]
    assert [item.name for item in runtime.providers.items] == ["DeepSeek"]


def test_upsert_passes_through_schema_extras(tmp_path: Path) -> None:
    service, _runtime, path = _service(tmp_path)
    service.upsert(
        _provider_payload(
            kind="embedding",
            embedding_url="https://api.deepseek.com/v1/embeddings",
            thinking_enable=True,
            thinking_budget=512,
            max_output_tokens=4096,
        )
    )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    (item,) = raw["providers"]["items"]
    assert item["kind"] == "embedding"
    assert item["embedding_url"] == "https://api.deepseek.com/v1/embeddings"
    assert item["thinking_enable"] is True
    assert item["thinking_budget"] == 512
    assert item["max_output_tokens"] == 4096


def test_upsert_absorbs_external_yaml_edits(tmp_path: Path) -> None:
    service, runtime, path = _service(tmp_path)
    service.upsert(_provider_payload())

    # Hand-edit the file the way an operator would.
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["providers"]["items"].append(
        {
            "name": "HandEdited",
            "kind": "chat",
            "api_format": "ollama",
            "base_url": "http://localhost:11434",
            "model": "llama3.3",
        }
    )
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    service.upsert(_provider_payload(name="Third", model="m3"))

    names = _names(path)
    assert names == ["DeepSeek", "HandEdited", "Third"]
    assert {item.name for item in runtime.providers.items} == {
        "DeepSeek",
        "HandEdited",
        "Third",
    }


# ---------------------------------------------------------------------------
# patch
# ---------------------------------------------------------------------------


def test_patch_updates_fields_and_empty_api_key_keeps_keys(tmp_path: Path) -> None:
    service, runtime, path = _service(tmp_path)
    service.upsert(_provider_payload())

    result = service.patch("deepseek", {"model": "deepseek-reasoner", "api_key": ""})
    assert result["status"] == "updated"
    assert result["provider"]["model"] == "deepseek-reasoner"

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    (item,) = raw["providers"]["items"]
    # Empty api_key must not erase the stored key.
    assert item["api_keys"] == ["sk-deepseek-testkey-0001"]

    rotated = service.patch("DeepSeek", {"api_key": "sk-rotated-9999"})
    assert rotated["provider"]["api_key_count"] == 1
    assert runtime.get_provider("DeepSeek", kind="chat").api_keys == ("sk-rotated-9999",)


def test_patch_set_default_and_errors(tmp_path: Path) -> None:
    service, runtime, path = _service(tmp_path)
    service.upsert(_provider_payload())
    service.upsert(_provider_payload(name="Second", model="m2", set_default=False))
    assert runtime.providers.default == "DeepSeek"

    service.patch("Second", {"set_default": True})
    assert runtime.providers.default == "Second"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["providers"]["default"] == "Second"
    assert raw["runtime_defaults"]["default_provider_name"] == "Second"

    with pytest.raises(ProviderNotFoundError):
        service.patch("missing", {"model": "x"})
    with pytest.raises(ProviderValidationError):
        service.patch("Second", {"name": "renamed"})
    with pytest.raises(ProviderValidationError):
        service.patch("Second", {"bogus": 1})


# ---------------------------------------------------------------------------
# delete + default reassignment
# ---------------------------------------------------------------------------


def _two_provider_seed() -> dict:
    return {
        "providers": {
            "default": "Alpha",
            "items": [
                {
                    "name": "Alpha",
                    "kind": "chat",
                    "api_format": "openai",
                    "base_url": "https://a.example/v1",
                    "model": "a-model",
                    "api_keys": ["sk-a"],
                },
                {
                    "name": "Beta",
                    "kind": "chat",
                    "api_format": "openai",
                    "base_url": "https://b.example/v1",
                    "model": "b-model",
                    "api_keys": ["sk-b"],
                },
            ],
        },
        "runtime_defaults": {"default_provider_name": "Alpha"},
    }


def test_delete_default_reassigns_to_remaining_chat_provider(tmp_path: Path) -> None:
    service, runtime, path = _service(tmp_path, payload=_two_provider_seed())

    result = service.delete("alpha")  # case-insensitive

    assert result == {"deleted": True, "name": "alpha", "default": "Beta"}
    assert _names(path) == ["Beta"]
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["providers"]["default"] == "Beta"
    assert raw["runtime_defaults"]["default_provider_name"] == "Beta"
    assert runtime.providers.default == "Beta"


def test_delete_last_default_provider_conflicts(tmp_path: Path) -> None:
    service, _runtime, path = _service(tmp_path)
    service.upsert(_provider_payload())
    before = path.read_text(encoding="utf-8")

    with pytest.raises(ProviderConflictError):
        service.delete("DeepSeek")

    assert path.read_text(encoding="utf-8") == before


def test_delete_nondefault_and_missing(tmp_path: Path) -> None:
    service, _runtime, _ = _service(tmp_path, payload=_two_provider_seed())

    result = service.delete("Beta")
    assert result["default"] == "Alpha"
    with pytest.raises(ProviderNotFoundError):
        service.delete("ghost")


# ---------------------------------------------------------------------------
# test() probe classification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_endpoint_probe_success_and_failure_dicts(tmp_path: Path) -> None:
    service, _runtime, _ = _service(tmp_path, probe=lambda p: {"ok": True})
    result = await service.test(_provider_payload(api_key="sk-unsaved-777"))
    assert result["ok"] is True
    assert result["error_kind"] is None
    assert result["name"] == "DeepSeek"
    assert "sk-unsaved-777" not in str(result)

    service, _runtime, _ = _service(
        tmp_path / "b", probe=lambda p: {"ok": False, "error_kind": "model"}
    )
    result = await service.test(
        {"name": "whatever", "api_format": "openai", "base_url": "https://x", "model": "m"}
    )
    assert result["ok"] is False
    assert result["error_kind"] == "model"


@pytest.mark.asyncio
async def test_test_endpoint_saved_name_probe(tmp_path: Path) -> None:
    seen: list[str] = []

    async def _probe(profile) -> dict:
        seen.append(profile.name)
        return {"ok": True}

    service, _runtime, _ = _service(tmp_path, payload=_two_provider_seed(), probe=_probe)
    result = await service.test({"name": "beta"})
    assert result["ok"] is True
    assert seen == ["Beta"]

    missing = await service.test({"name": "ghost"})
    assert missing["ok"] is False
    assert missing["error_kind"] == "unknown"
    assert "not configured" in missing["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (
            LLMAuthError(error_type="authentication_error", message="bad key"),
            "auth",
        ),
        (
            LLMNetworkError(error_type="network_error", message="connect failed"),
            "network",
        ),
        (
            LLMProviderError(
                error_type="provider_response_error",
                message="model 'nope' does not exist",
            ),
            "model",
        ),
        (
            LLMProviderError(
                error_type="not_json",
                message="provider returned non-JSON content",
            ),
            "schema",
        ),
        (RuntimeError("totally unexpected"), "unknown"),
    ],
)
async def test_test_endpoint_error_kind_categories(
    tmp_path: Path, raised: Exception, expected: str
) -> None:
    def _probe(profile):
        raise raised

    service, _runtime, _ = _service(tmp_path, probe=_probe)
    result = await service.test(_provider_payload())
    assert result["ok"] is False
    assert result["error_kind"] == expected


@pytest.mark.asyncio
async def test_test_endpoint_redacts_key_material_in_errors(tmp_path: Path) -> None:
    def _probe(profile):
        raise RuntimeError("upstream echoed sk-deepseek-testkey-0001 back")

    service, _runtime, _ = _service(tmp_path, probe=_probe)
    result = await service.test(_provider_payload())
    assert result["ok"] is False
    assert "sk-deepseek-testkey-0001" not in result["message"]
    assert "***" in result["message"]


@pytest.mark.asyncio
async def test_test_endpoint_http_status_classification(tmp_path: Path) -> None:
    class _StatusError(Exception):
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            super().__init__(f"http {status_code}")

    for status, expected in ((401, "auth"), (403, "auth"), (404, "model")):

        def _probe(profile, _status=status):
            raise _StatusError(_status)

        service, _runtime, _ = _service(tmp_path / str(status), probe=_probe)
        result = await service.test(_provider_payload())
        assert result["error_kind"] == expected


# ---------------------------------------------------------------------------
# list_models
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_models_falls_back_when_unreachable(tmp_path: Path) -> None:
    seed = _two_provider_seed()
    seed["providers"]["items"][0]["base_url"] = "http://127.0.0.1:1"
    service, _runtime, _ = _service(tmp_path, payload=seed)

    # 127.0.0.1:1 refuses instantly — the fallback lists profile+template models.
    result = await service.list_models("alpha")
    assert result["name"] == "Alpha"
    assert result["source"] == "fallback"
    assert "a-model" in result["models"]

    with pytest.raises(ProviderNotFoundError):
        await service.list_models("ghost")


# ---------------------------------------------------------------------------
# templates / list_profiles
# ---------------------------------------------------------------------------


def test_templates_match_provider_templates(tmp_path: Path) -> None:
    service, _runtime, _ = _service(tmp_path)
    assert service.templates() == provider_templates()


def test_list_profiles_returns_masked_views(tmp_path: Path) -> None:
    service, _runtime, _ = _service(tmp_path, payload=_two_provider_seed())

    profiles = service.list_profiles()
    assert [p["name"] for p in profiles] == ["Alpha", "Beta"]
    assert profiles[0]["api_key_configured"] is True
    assert profiles[0]["api_key_count"] == 1
    assert "sk-a" not in str(profiles)


# ---------------------------------------------------------------------------
# review-fix regressions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_endpoint_drops_stored_keys_on_rerouted_probe(
    tmp_path: Path,
) -> None:
    """A stored profile probed at a caller-chosen base_url must not carry
    stored key material — that would be a credential exfiltration primitive."""

    captured: dict = {}

    def _probe(profile) -> dict:
        captured["api_keys"] = list(profile.api_keys)
        captured["base_url"] = profile.base_url
        return {"ok": True}

    service, _runtime, _ = _service(tmp_path, payload=_two_provider_seed(), probe=_probe)
    result = await service.test({"name": "Alpha", "base_url": "https://attacker.example"})
    assert result["ok"] is True
    assert captured["base_url"] == "https://attacker.example"
    assert captured["api_keys"] == []


@pytest.mark.asyncio
async def test_test_endpoint_keeps_stored_keys_when_probe_unchanged(
    tmp_path: Path,
) -> None:
    captured: dict = {}

    def _probe(profile) -> dict:
        captured["api_keys"] = list(profile.api_keys)
        return {"ok": True}

    service, _runtime, _ = _service(tmp_path, payload=_two_provider_seed(), probe=_probe)
    # model-only override: same endpoint/auth shape — stored keys retained.
    await service.test({"name": "Alpha", "model": "other-model"})
    assert captured["api_keys"] != []


@pytest.mark.asyncio
async def test_test_endpoint_explicit_api_key_still_works_on_reroute(
    tmp_path: Path,
) -> None:
    captured: dict = {}

    def _probe(profile) -> dict:
        captured["api_keys"] = list(profile.api_keys)
        return {"ok": True}

    service, _runtime, _ = _service(tmp_path, payload=_two_provider_seed(), probe=_probe)
    await service.test(
        {
            "name": "Alpha",
            "base_url": "https://other.example",
            "api_key": "sk-caller-supplied",
        }
    )
    assert captured["api_keys"] == ["sk-caller-supplied"]


def test_set_default_rejected_for_embedding_provider(tmp_path: Path) -> None:
    service, _runtime, _ = _service(tmp_path, payload=_two_provider_seed())
    service.upsert(
        _provider_payload(name="Emb", kind="embedding", embedding_url="https://emb.example")
    )
    with pytest.raises(ProviderValidationError):
        service.patch("Emb", {"set_default": True})


def test_upsert_rejects_set_default_for_embedding(tmp_path: Path) -> None:
    service, _runtime, _ = _service(tmp_path)
    with pytest.raises(ProviderValidationError):
        service.upsert(
            _provider_payload(
                name="Emb",
                kind="embedding",
                set_default=True,
                embedding_url="https://emb.example",
            )
        )


def test_first_embedding_provider_does_not_auto_default(tmp_path: Path) -> None:
    service, _runtime, path = _service(tmp_path)
    service.upsert(
        _provider_payload(name="Emb", kind="embedding", embedding_url="https://emb.example")
    )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["providers"].get("default") != "Emb"


def test_set_default_hot_applies_runtime_defaults(tmp_path: Path) -> None:
    service, runtime, _ = _service(tmp_path, payload=_two_provider_seed())
    assert runtime.runtime_defaults.default_provider_name == "Alpha"
    service.patch("Beta", {"set_default": True})
    assert runtime.runtime_defaults.default_provider_name == "Beta"


def test_delete_default_hot_applies_runtime_defaults(tmp_path: Path) -> None:
    service, runtime, _ = _service(tmp_path, payload=_two_provider_seed())
    service.delete("Alpha")
    assert runtime.runtime_defaults.default_provider_name == "Beta"


def test_provider_name_charset_rejected(tmp_path: Path) -> None:
    service, _runtime, _ = _service(tmp_path)
    with pytest.raises(ProviderValidationError):
        service.upsert(_provider_payload(name="bad/name?x"))


def test_list_profiles_exposes_keyless_flag(tmp_path: Path) -> None:
    seed = _two_provider_seed()
    seed["providers"]["items"].append(
        {
            "name": "Local",
            "kind": "chat",
            "api_format": "ollama",
            "base_url": "http://localhost:11434",
            "model": "llama3.3",
            "api_keys": [],
        }
    )
    service, _runtime, _ = _service(tmp_path, payload=seed)
    profiles = {p["name"]: p for p in service.list_profiles()}
    assert profiles["Local"]["keyless"] is True
    assert profiles["Alpha"]["keyless"] is False


def test_written_item_omits_deprecated_transport(tmp_path: Path) -> None:
    service, _runtime, path = _service(tmp_path)
    service.upsert(_provider_payload())
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "transport" not in raw["providers"]["items"][0]
