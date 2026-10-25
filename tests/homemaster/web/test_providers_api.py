"""HTTP-level tests for the provider-management endpoints (P0-2)."""

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace

import yaml
from fastapi.testclient import TestClient

from homemaster.application import RunRequest, SessionManager
from homemaster.config.config import HomeMasterConfig
from homemaster.events.bus import EventBus
from homemaster.providers.errors import LLMAuthError, LLMNetworkError
from homemaster.services.provider_config import ProviderConfigService
from homemaster.web.app import create_web_app
from homemaster.web.confirmations import WebConfirmationHandler

API_KEY = "sk-live-should-never-leak-4242"


class _FakeApplication:
    def __init__(self) -> None:
        self.event_bus = EventBus()
        self.session_manager = SessionManager()
        self.run_requests: list[RunRequest] = []
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def run(self, request: RunRequest):
        self.run_requests.append(request)
        return SimpleNamespace(status="replied", run_id="run-01")

    def cancel(self, session_id: str) -> bool:
        return False

    async def aclose(self) -> None:
        await self.event_bus.aclose()


def _stack(
    tmp_path: Path, *, probe=None
) -> tuple[TestClient, ProviderConfigService, HomeMasterConfig, Path]:
    config_path = tmp_path / "config.yaml"
    config = HomeMasterConfig.model_validate({})
    config._config_path = config_path
    service = ProviderConfigService(config_path=config_path, runtime_config=config, probe=probe)
    app = create_web_app(
        application=_FakeApplication(),
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
        config=config,
        provider_config_service=service,
    )
    return TestClient(app), service, config, config_path


def _payload(**overrides) -> dict:
    payload = {
        "name": "DeepSeek",
        "api_format": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key": API_KEY,
    }
    payload.update(overrides)
    return payload


def _config_items(path: Path) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(raw["providers"]["items"])


# ---------------------------------------------------------------------------
# lifecycle: POST → GET → PATCH → DELETE
# ---------------------------------------------------------------------------


def test_provider_crud_lifecycle_over_http(tmp_path: Path) -> None:
    client, _service, config, config_path = _stack(tmp_path)
    with client:
        created = client.post("/api/providers", json=_payload())
        assert created.status_code == 200
        body = created.json()
        assert body["status"] == "created"
        assert body["provider"]["name"] == "DeepSeek"
        assert body["provider"]["api_key_configured"] is True
        assert API_KEY not in created.text

        listed = client.get("/api/providers")
        assert listed.status_code == 200
        (entry,) = listed.json()["providers"]
        assert entry == {
            "name": "DeepSeek",
            "kind": "chat",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com",
            "api_format": "deepseek",
            "auth_type": "api_key",
            "api_key_configured": True,
            "api_key_count": 1,
            "keyless": False,
        }
        assert API_KEY not in listed.text

        # Hot apply: the live config object resolves the provider now.
        provider = config.get_provider("DeepSeek", kind="chat")
        assert provider.api_keys == (API_KEY,)

        patched = client.patch(
            "/api/providers/DeepSeek",
            json={"model": "deepseek-reasoner", "api_key": ""},
        )
        assert patched.status_code == 200
        assert patched.json()["provider"]["model"] == "deepseek-reasoner"
        # Empty api_key keeps the stored credential.
        assert config.get_provider("DeepSeek", kind="chat").api_keys == (API_KEY,)

        deleted = client.delete("/api/providers/DeepSeek")
        assert deleted.status_code == 409  # deleting the only/default provider
        assert deleted.json()["code"] == "provider_conflict"

        # With a replacement configured the delete succeeds and moves default.
        second = client.post(
            "/api/providers",
            json=_payload(name="Second", api_key="sk-second-1", set_default=False),
        )
        assert second.status_code == 200
        deleted = client.delete("/api/providers/DeepSeek")
        assert deleted.status_code == 200
        assert deleted.json() == {
            "deleted": True,
            "name": "DeepSeek",
            "default": "Second",
        }
        assert config.providers.default == "Second"

        gone = client.get("/api/providers")
        assert [p["name"] for p in gone.json()["providers"]] == ["Second"]
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_provider_templates_endpoint(tmp_path: Path) -> None:
    client, _service, _config, _path = _stack(tmp_path)
    with client:
        response = client.get("/api/provider-templates")
    assert response.status_code == 200
    templates = response.json()["templates"]
    keys = [t["key"] for t in templates]
    assert "anthropic" in keys and "openai" in keys
    assert keys[-1] == "openai-compatible"


def test_provider_models_endpoint_404_and_fallback(tmp_path: Path) -> None:
    client, _service, _config, _path = _stack(tmp_path)
    with client:
        missing = client.get("/api/providers/ghost/models")
        assert missing.status_code == 404
        assert missing.json()["code"] == "provider_not_found"

        client.post(
            "/api/providers",
            json=_payload(base_url="http://127.0.0.1:1"),
        )
        listed = client.get("/api/providers/DeepSeek/models")
        assert listed.status_code == 200
        body = listed.json()
        assert body["source"] == "fallback"
        assert "deepseek-chat" in body["models"]


# ---------------------------------------------------------------------------
# error branches: 404 / 409 / 422
# ---------------------------------------------------------------------------


def test_provider_error_branches(tmp_path: Path) -> None:
    client, _service, _config, config_path = _stack(tmp_path)
    with client:
        assert client.patch("/api/providers/ghost", json={"model": "m"}).status_code == 404
        assert client.delete("/api/providers/ghost").status_code == 404

        bad_field = client.post("/api/providers", json=_payload(bogus=1))
        assert bad_field.status_code == 422
        assert bad_field.json()["code"] == "invalid_provider"

        bad_format = client.post("/api/providers", json=_payload(api_format="nope"))
        assert bad_format.status_code == 422
        # Validation failures must not touch the file.
        assert not config_path.exists()

        client.post("/api/providers", json=_payload())
        conflict = client.post("/api/providers", json=_payload(model="different-model"))
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "provider_conflict"

        overwritten = client.post(
            "/api/providers?overwrite=true",
            json=_payload(model="different-model"),
        )
        assert overwritten.status_code == 200
        assert overwritten.json()["status"] == "updated"


# ---------------------------------------------------------------------------
# POST /api/providers/test — always 200, classification in body
# ---------------------------------------------------------------------------


def test_provider_test_endpoint_with_injected_probe(tmp_path: Path) -> None:
    def _probe(profile):
        if profile.name == "BadKey":
            raise LLMAuthError(error_type="authentication_error", message="denied")
        if profile.name == "BadUrl":
            raise LLMNetworkError(error_type="network_error", message="refused")
        return {"ok": True}

    client, _service, _config, _path = _stack(tmp_path, probe=_probe)
    with client:
        ok = client.post("/api/providers/test", json=_payload())
        assert ok.status_code == 200
        assert ok.json()["ok"] is True
        assert ok.json()["error_kind"] is None
        assert API_KEY not in ok.text

        auth = client.post("/api/providers/test", json=_payload(name="BadKey"))
        assert auth.status_code == 200
        assert auth.json()["ok"] is False
        assert auth.json()["error_kind"] == "auth"

        net = client.post("/api/providers/test", json=_payload(name="BadUrl"))
        assert net.status_code == 200
        assert net.json()["error_kind"] == "network"

        # Unknown stored provider still returns 200 with ok:false.
        missing = client.post("/api/providers/test", json={"name": "ghost"})
        assert missing.status_code == 200
        assert missing.json()["ok"] is False


def test_provider_test_endpoint_saved_name(tmp_path: Path) -> None:
    seen: list[str] = []

    def _probe(profile):
        seen.append(profile.name)
        return {"ok": True}

    client, _service, _config, _path = _stack(tmp_path, probe=_probe)
    with client:
        client.post("/api/providers", json=_payload())
        result = client.post("/api/providers/test", json={"name": "deepseek"})
        assert result.status_code == 200
        assert result.json()["ok"] is True
        assert seen == ["DeepSeek"]


# ---------------------------------------------------------------------------
# hot apply through the real selection path
# ---------------------------------------------------------------------------


def test_new_provider_passes_selection_validation_immediately(
    tmp_path: Path,
) -> None:
    client, _service, _config, _path = _stack(tmp_path)
    with client:
        session_id = client.post("/api/sessions").json()["session_id"]

        rejected = client.post(
            f"/api/sessions/{session_id}/messages",
            json={
                "request_id": "req-1",
                "text": "hi",
                "provider_name": "DeepSeek",
            },
        )
        assert rejected.status_code == 422
        assert rejected.json()["code"] == "unknown_provider"

        client.post("/api/providers", json=_payload())

        # The same provider_name now clears selection validation; the request
        # only stops at the missing event stream — provider resolution passed.
        accepted = client.post(
            f"/api/sessions/{session_id}/messages",
            json={
                "request_id": "req-2",
                "text": "hi",
                "provider_name": "DeepSeek",
            },
        )
        assert accepted.status_code == 409
        assert accepted.json()["code"] == "event_stream_not_ready"


def test_external_yaml_edit_is_absorbed_on_next_write(tmp_path: Path) -> None:
    client, _service, config, config_path = _stack(tmp_path)
    with client:
        client.post("/api/providers", json=_payload())

        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        raw["providers"]["items"].append(
            {
                "name": "HandEdited",
                "kind": "chat",
                "api_format": "ollama",
                "base_url": "http://localhost:11434",
                "model": "llama3.3",
            }
        )
        config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

        client.patch("/api/providers/DeepSeek", json={"model": "deepseek-v3"})

        names = [item.name for item in config.providers.items]
        assert names == ["DeepSeek", "HandEdited"]
        listed_names = [item["name"] for item in _config_items(config_path)]
        assert listed_names == ["DeepSeek", "HandEdited"]
