"""Tests for the ``homemaster auth`` credential wizard (W3-1)."""

from __future__ import annotations

import stat
from collections import deque
from pathlib import Path
from typing import get_args

import pytest
import yaml

from homemaster.cli.auth import (
    ProviderAnswers,
    auth_command,
    build_provider_item,
    env_override_hint,
    mask_api_key,
    merge_providers_section,
    minimal_config_payload,
    provider_templates,
    resolve_auth_config_path,
    run_auth_wizard,
    write_auth_config,
)
from homemaster.config import load_config
from homemaster.config.config import ApiFormatName


def _answers(**overrides) -> ProviderAnswers:
    values = {
        "name": "Mimo",
        "api_format": "anthropic",
        "transport": "anthropic_sdk",
        "auth_type": "api_key",
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-4-5",
        "api_key": "sk-ant-test1234567890",
        "context_window_tokens": 200000,
        "set_default": True,
    }
    values.update(overrides)
    return ProviderAnswers(**values)


class _ScriptedPrompter:
    """Deterministic ``_AuthPrompter`` fed from queues ("" = accept default)."""

    def __init__(
        self,
        *,
        texts: list[str] | None = None,
        secrets: list[str] | None = None,
        confirms: list[bool] | None = None,
        choices: list[int] | None = None,
    ) -> None:
        self._texts = deque(texts or [])
        self._secrets = deque(secrets or [])
        self._confirms = deque(confirms or [])
        self._choices = deque(choices or [])

    def text(self, message: str, *, default: str = "") -> str:
        if not self._texts:
            raise AssertionError(f"unexpected text prompt: {message}")
        raw = self._texts.popleft()
        return raw or default

    def secret(self, message: str) -> str:
        if not self._secrets:
            raise AssertionError(f"unexpected secret prompt: {message}")
        return self._secrets.popleft()

    def ask_yes_no(self, message: str, *, default: bool = True) -> bool:
        if not self._confirms:
            raise AssertionError(f"unexpected confirm prompt: {message}")
        return self._confirms.popleft()

    def choose(self, message: str, options: list[str], *, default_index: int = 0) -> int:
        if not self._choices:
            raise AssertionError(f"unexpected choose prompt: {message}")
        return self._choices.popleft()


# ---------------------------------------------------------------------------
# templates & masking
# ---------------------------------------------------------------------------


def test_templates_cover_every_api_format_plus_openai_compatible() -> None:
    templates = provider_templates()
    keys = [t.key for t in templates]
    for api_format in get_args(ApiFormatName):
        assert api_format in keys
    assert keys[-1] == "openai-compatible"
    for template in templates:
        assert template.api_format in get_args(ApiFormatName)


def test_ollama_template_is_keyless() -> None:
    ollama = next(t for t in provider_templates() if t.key == "ollama")
    assert ollama.keyless is True


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("", "(not stored)"),
        ("short", "***"),
        ("sk-ant-abcdefghijklmnopqrstuvwxyz", "sk-***wxyz"),
        ("tp-cf45sqvijwqzb8iajlpcx36re5ykot0u088v47vxy1nynfed", "tp-***nfed"),
        ("x" * 40, "***" + "x" * 4),
    ],
)
def test_mask_api_key(key: str, expected: str) -> None:
    assert mask_api_key(key) == expected


def test_env_override_hint_normalizes_name() -> None:
    assert env_override_hint("Mimo") == "HOMEMASTER_MIMO_API_KEY"
    assert env_override_hint("my-provider") == "HOMEMASTER_MY_PROVIDER_API_KEY"


# ---------------------------------------------------------------------------
# path resolution
# ---------------------------------------------------------------------------


def test_resolve_path_prefers_explicit_config() -> None:
    assert resolve_auth_config_path("/tmp/x/config.yaml") == Path("/tmp/x/config.yaml")


def test_resolve_path_uses_homemaster_home() -> None:
    target = resolve_auth_config_path(environ={"HOMEMASTER_HOME": "/srv/hm"})
    assert target == Path("/srv/hm/config.yaml")


def test_resolve_path_defaults_to_home_dot_homemaster() -> None:
    target = resolve_auth_config_path(environ={"HOME": "/home/u"})
    assert target == Path("/home/u/.homemaster/config.yaml")


# ---------------------------------------------------------------------------
# merge semantics
# ---------------------------------------------------------------------------


def test_merge_preserves_unrelated_keys_and_appends(tmp_path: Path) -> None:
    existing = {
        "providers": {
            "default": "OldOne",
            "items": [
                {"name": "OldOne", "api_format": "openai", "base_url": "https://o", "model": "m"}
            ],
        },
        "memory": {"mode": "full", "neo4j": {"password": "keep-me"}},
        "gateway": {"enabled": True},
    }
    merged = merge_providers_section(existing, _answers(name="Mimo", set_default=False))

    # Other sections untouched.
    assert merged["memory"] == existing["memory"]
    assert merged["gateway"] == existing["gateway"]
    # New item appended, old one preserved, default not switched.
    names = [item["name"] for item in merged["providers"]["items"]]
    assert names == ["OldOne", "Mimo"]
    assert merged["providers"]["default"] == "OldOne"
    assert "runtime_defaults" not in merged


def test_merge_replaces_same_name_provider_in_place() -> None:
    existing = {
        "providers": {
            "default": "mimo",
            "items": [
                {"name": "mimo", "api_format": "openai", "base_url": "https://old", "model": "m"},
                {"name": "Other", "api_format": "openai", "base_url": "https://x", "model": "y"},
            ],
        }
    }
    merged = merge_providers_section(existing, _answers(name="Mimo", set_default=True))

    assert len(merged["providers"]["items"]) == 2
    updated = merged["providers"]["items"][0]
    assert updated["name"] == "Mimo"
    assert updated["api_format"] == "anthropic"
    assert merged["providers"]["items"][1]["name"] == "Other"
    assert merged["providers"]["default"] == "Mimo"
    assert merged["runtime_defaults"]["default_provider_name"] == "Mimo"


def test_merge_into_providers_less_document() -> None:
    merged = merge_providers_section({"skills": {"allow_project": False}}, _answers())
    assert merged["skills"] == {"allow_project": False}
    assert merged["providers"]["items"][0]["name"] == "Mimo"
    assert merged["providers"]["default"] == "Mimo"
    assert merged["runtime_defaults"]["default_provider_name"] == "Mimo"


def test_build_provider_item_matches_schema_and_loads(tmp_path: Path) -> None:
    item = build_provider_item(_answers(api_key=""))
    assert item["api_keys"] == []
    config_file = tmp_path / "config.yaml"
    write_auth_config(config_file, minimal_config_payload(_answers()))
    config = load_config(config_file)
    provider = config.get_provider("Mimo", kind="chat")
    assert provider.api_format == "anthropic"
    assert provider.model == "claude-sonnet-4-5"


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


def test_write_auth_config_creates_file_with_600(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "config.yaml"
    write_auth_config(target, minimal_config_payload(_answers()))

    assert target.is_file()
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600, oct(mode)
    # And the written document parses through the real loader.
    config = load_config(target)
    assert config.providers.default == "Mimo"
    assert config.runtime_defaults.default_provider_name == "Mimo"
    assert config.memory.mode == "files"


def test_minimal_config_loads_without_required_sections(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    write_auth_config(target, minimal_config_payload(_answers(name="DeepSeek")))
    config = load_config(target)
    provider = config.get_provider(kind="chat")
    assert provider.name == "DeepSeek"


# ---------------------------------------------------------------------------
# print-only / non-TTY degradation
# ---------------------------------------------------------------------------


def test_print_only_prints_snippet_and_writes_nothing(tmp_path: Path, capsys) -> None:
    target = tmp_path / "config.yaml"
    out: list[str] = []
    code = auth_command(
        print_only=True,
        config_path=target,
        echo=out.append,
    )
    assert code == 0
    text = "\n".join(out)
    assert str(target) in text
    assert "providers:" in text
    assert "api_format: anthropic" in text
    assert "<your-api-key>" in text
    assert not target.exists()


def test_non_tty_degrades_to_print_only(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    out: list[str] = []
    code = auth_command(config_path=target, interactive=False, echo=out.append)
    assert code == 0
    assert "non-interactive" in "\n".join(out)
    assert not target.exists()


def test_print_only_existing_target_mentions_merge(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text("providers:\n  default: Keep\n", encoding="utf-8")
    out: list[str] = []
    code = auth_command(print_only=True, config_path=target, echo=out.append)
    assert code == 0
    text = "\n".join(out)
    assert "already exists" in text
    assert "providers.items" in text
    # Existing content untouched.
    assert yaml.safe_load(target.read_text()) == {"providers": {"default": "Keep"}}


# ---------------------------------------------------------------------------
# interactive wizard (scripted prompter)
# ---------------------------------------------------------------------------


def _anthropic_script(
    *,
    confirms: list[bool],
    api_key: str = "sk-ant-livekey00001111",
    name: str = "",
) -> _ScriptedPrompter:
    return _ScriptedPrompter(
        choices=[0, 0],  # template: anthropic; auth_type: api_key
        texts=[name, "", "", ""],  # name, base_url, model, context window
        secrets=[api_key],
        confirms=confirms,
    )


def test_wizard_creates_config_end_to_end(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    prompter = _anthropic_script(confirms=[True, True])  # set default + write
    out: list[str] = []
    code = run_auth_wizard(target_path=target, prompter=prompter, echo=out.append)

    assert code == 0
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    config = load_config(target)
    provider = config.get_provider(kind="chat")
    assert provider.name == "Mimo"
    assert provider.api_keys == ("sk-ant-livekey00001111",)
    assert provider.base_url == "https://api.anthropic.com"
    # Masked key appears in the confirmation echo, raw key never does.
    echoed = "\n".join(out)
    assert "sk-***1111" in echoed
    assert "sk-ant-livekey00001111" not in echoed
    assert "doctor --live" in echoed


def test_wizard_merge_preserves_existing_config(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "default": "Keep",
                    "items": [
                        {
                            "name": "Keep",
                            "api_format": "openai",
                            "base_url": "https://keep",
                            "model": "m",
                        }
                    ],
                },
                "memory": {"mode": "full", "data_root": "/data"},
                "gateway": {"enabled": True},
            }
        ),
        encoding="utf-8",
    )
    prompter = _anthropic_script(confirms=[False, True])  # keep default + write
    code = run_auth_wizard(target_path=target, prompter=prompter, echo=lambda _s: None)

    assert code == 0
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert raw["memory"] == {"mode": "full", "data_root": "/data"}
    assert raw["gateway"] == {"enabled": True}
    assert raw["providers"]["default"] == "Keep"
    names = [item["name"] for item in raw["providers"]["items"]]
    assert names == ["Keep", "Mimo"]
    # Runtime still resolves the original default provider.
    config = load_config(target)
    assert config.providers.default == "Keep"


def test_wizard_decline_write_leaves_no_trace(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    prompter = _anthropic_script(confirms=[True, False])  # default yes, write no
    out: list[str] = []
    code = run_auth_wizard(target_path=target, prompter=prompter, echo=out.append)
    assert code == 1
    assert not target.exists()
    assert "nothing was written" in "\n".join(out)


def test_wizard_refuses_invalid_existing_yaml(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text("just-a-string", encoding="utf-8")
    prompter = _anthropic_script(confirms=[True, True])
    out: list[str] = []
    code = run_auth_wizard(target_path=target, prompter=prompter, echo=out.append)
    assert code == 1
    assert "error" in "\n".join(out)
    assert target.read_text(encoding="utf-8") == "just-a-string"


def test_wizard_ollama_skips_api_key(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    ollama_index = [t.key for t in provider_templates()].index("ollama")
    prompter = _ScriptedPrompter(
        choices=[ollama_index],  # ollama template (no auth_type prompt)
        texts=["", "", "", ""],  # name/base_url/model/context defaults
        secrets=[],  # keyless: secret() must never be called
        confirms=[True, True],
    )
    code = run_auth_wizard(target_path=target, prompter=prompter, echo=lambda _s: None)
    assert code == 0
    provider = load_config(target).get_provider(kind="chat")
    assert provider.api_format == "ollama"
    assert provider.api_keys == ()


def test_wizard_anthropic_auth_token_choice(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    prompter = _ScriptedPrompter(
        choices=[0, 1],  # anthropic template; auth_token
        texts=["", "", "", ""],
        secrets=["tok-abcdefghijklmnop"],
        confirms=[True, True],
    )
    code = run_auth_wizard(target_path=target, prompter=prompter, echo=lambda _s: None)
    assert code == 0
    provider = load_config(target).get_provider(kind="chat")
    assert provider.auth_type == "auth_token"
    assert provider.api_keys == ("tok-abcdefghijklmnop",)


def test_wizard_empty_key_allowed_with_env_hint(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    prompter = _anthropic_script(confirms=[True, True], api_key="")
    out: list[str] = []
    code = run_auth_wizard(target_path=target, prompter=prompter, echo=out.append)
    assert code == 0
    assert "HOMEMASTER_MIMO_API_KEY" in "\n".join(out)
    provider = load_config(target).get_provider(kind="chat")
    assert provider.api_keys == ()


def test_wizard_custom_openai_compatible(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    custom_index = len(provider_templates()) - 1
    prompter = _ScriptedPrompter(
        choices=[custom_index],
        texts=["MyGW", "https://gateway.example/v1", "my-model", "128000"],
        secrets=["gw-secret-key-9999"],
        confirms=[True, True],
    )
    code = run_auth_wizard(target_path=target, prompter=prompter, echo=lambda _s: None)
    assert code == 0
    config = load_config(target)
    provider = config.get_provider("MyGW", kind="chat")
    assert provider.api_format == "openai"
    assert provider.base_url == "https://gateway.example/v1"
    assert provider.context_window_tokens == 128000
    assert config.providers.default == "MyGW"
    assert config.runtime_defaults.default_provider_name == "MyGW"


def test_wizard_interactive_command_path(tmp_path: Path) -> None:
    """``auth_command`` drives the wizard when interactive is forced on."""

    target = tmp_path / "config.yaml"
    prompter = _anthropic_script(confirms=[True, True])
    code = auth_command(
        config_path=target,
        interactive=True,
        prompter=prompter,
        echo=lambda _s: None,
    )
    assert code == 0
    assert load_config(target).providers.default == "Mimo"


def test_auth_command_keyboard_interrupt_returns_130(tmp_path: Path) -> None:
    class _Boom:
        def text(self, message: str, *, default: str = "") -> str:
            raise KeyboardInterrupt

        def secret(self, message: str) -> str:
            raise KeyboardInterrupt

        def ask_yes_no(self, message: str, *, default: bool = True) -> bool:
            raise KeyboardInterrupt

        def choose(self, message: str, options: list[str], *, default_index: int = 0) -> int:
            raise KeyboardInterrupt

    target = tmp_path / "config.yaml"
    out: list[str] = []
    code = auth_command(
        config_path=target,
        interactive=True,
        prompter=_Boom(),
        echo=out.append,
    )
    assert code == 130
    assert not target.exists()
