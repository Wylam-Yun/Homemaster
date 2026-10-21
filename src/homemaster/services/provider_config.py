"""Provider profile management shared by ``homemaster auth`` and the web API.

The first half of this module is the pure-function layer moved verbatim from
``cli/auth.py``: provider templates, masked-key rendering, auth-config path
resolution, the ``providers``-section merge, the atomic ``0600`` config write
and the existing-payload loaders.  ``cli/auth.py`` imports those names back,
so the interactive wizard keeps its exact behaviour.

The second half is ``ProviderConfigService`` — the
``reload → merge → validate → write → hot-apply`` pipeline behind the web
management endpoints:

* Every write re-reads the current YAML, merges the change into the
  ``providers`` section, validates the **whole** document through
  ``HomeMasterConfig.model_validate`` and only then hits the disk
  (``write_auth_config``: mkstemp + chmod 0600 + ``os.replace``).
* ``runtime_config`` is the same ``HomeMasterConfig`` object the
  application's provider-factory closure re-reads on every run, so assigning
  the freshly validated ``providers`` section makes profile changes effective
  on the next run without a restart.  Other sections — memory, mcp,
  permissions, ``runtime_defaults`` — stay restart-scoped.  Env-var overrides
  (``HOMEMASTER_<NAME>_API_KEY`` and friends, applied by ``load_config``) are
  re-applied to the hot-applied section so providers whose keys live only in
  the environment keep working.
* Because every write endpoint reloads the file first, hand-edits to the
  YAML are absorbed on the next write call — that is the reload path
  (deliberately chosen over a separate ``POST /api/providers/reload``).
* API keys are write-only: every response view carries counts/flags, never
  key material.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import stat
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args
from urllib.parse import urlsplit

import httpx
import yaml
from pydantic import ValidationError

from homemaster.config.config import (
    ANTHROPIC_WIRE_FORMATS,
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    ApiFormatName,
    ConfigError,
    HomeMasterConfig,
    ProviderProfileConfig,
    _apply_env_overrides,
    _homemaster_home,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Moved verbatim from cli/auth.py — keep behaviour identical.
# ---------------------------------------------------------------------------

# Suggested defaults per ``ApiFormatName``.  base_url values mirror the
# vendored AgentScope credential defaults (``src/agentscope/credential/_*.py``);
# model ids are editable suggestions, not validated against the provider.
_TEMPLATE_DEFAULTS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "title": "Anthropic (Claude)",
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-4-5",
        "transport": "anthropic_sdk",
        "supports_auth_token": True,
    },
    "minimax": {
        "title": "MiniMax (Anthropic-compatible)",
        "base_url": "https://api.minimax.io/anthropic",
        "model": "MiniMax-M2",
        "transport": "anthropic_sdk",
        "supports_auth_token": False,
    },
    "openai": {
        "title": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5",
        "transport": "openai_sdk",
        "supports_auth_token": False,
    },
    "dashscope": {
        "title": "DashScope (Alibaba Qwen)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3-max",
        "transport": "openai_sdk",
        "supports_auth_token": False,
    },
    "deepseek": {
        "title": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "transport": "openai_sdk",
        "supports_auth_token": False,
    },
    "moonshot": {
        "title": "Moonshot (Kimi)",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "kimi-k2-0905-preview",
        "transport": "openai_sdk",
        "supports_auth_token": False,
    },
    "volcengine": {
        "title": "Volcengine Ark (Doubao)",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-1-6-250615",
        "transport": "openai_sdk",
        "supports_auth_token": False,
    },
    "ollama": {
        "title": "Ollama (local, keyless)",
        "base_url": "http://localhost:11434",
        "model": "llama3.3",
        "transport": "openai_sdk",
        "supports_auth_token": False,
        "keyless": True,
    },
}

_OPENAI_COMPATIBLE_KEY = "openai-compatible"


@dataclass(frozen=True)
class ProviderTemplate:
    """One selectable row in the wizard's provider menu."""

    key: str
    title: str
    api_format: str
    base_url: str  # suggested default; "" means the user must supply one
    model: str  # suggested default; "" means the user must supply one
    transport: str
    supports_auth_token: bool
    keyless: bool = False


@dataclass(frozen=True)
class ProviderAnswers:
    """Everything needed to render one ``providers.items`` entry."""

    name: str
    api_format: str
    transport: str
    auth_type: str
    base_url: str
    model: str
    api_key: str  # "" = none stored; keyless providers always ""
    context_window_tokens: int
    set_default: bool


def provider_templates() -> tuple[ProviderTemplate, ...]:
    """Menu rows: one per real ``ApiFormatName`` member + openai-compatible.

    Deriving the list from the Literal keeps the menu in lockstep with the
    schema; an enum member without curated defaults falls back to a generic
    OpenAI-wire row so the template list never drifts behind the type.
    """

    templates: list[ProviderTemplate] = []
    for api_format in get_args(ApiFormatName):
        defaults = _TEMPLATE_DEFAULTS.get(api_format, {})
        templates.append(
            ProviderTemplate(
                key=api_format,
                title=defaults.get("title", api_format),
                api_format=api_format,
                base_url=defaults.get("base_url", ""),
                model=defaults.get("model", ""),
                transport=defaults.get("transport", "openai_sdk"),
                supports_auth_token=bool(defaults.get("supports_auth_token", False)),
                keyless=bool(defaults.get("keyless", False)),
            )
        )
    templates.append(
        ProviderTemplate(
            key=_OPENAI_COMPATIBLE_KEY,
            title="openai-compatible (custom base_url)",
            api_format="openai",
            base_url="",
            model="",
            transport="openai_sdk",
            supports_auth_token=False,
        )
    )
    return tuple(templates)


def mask_api_key(key: str) -> str:
    """``sk-...wxyz`` style masking for the confirmation page."""

    stripped = key.strip()
    if not stripped:
        return "(not stored)"
    if len(stripped) <= 8:
        return "***"
    head = ""
    first_dash = stripped.find("-")
    if 0 < first_dash <= 8:
        head = stripped[: first_dash + 1]
    return f"{head}***{stripped[-4:]}"


def resolve_auth_config_path(
    config_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Target file for the wizard: --config > $HOMEMASTER_HOME/config.yaml.

    Unlike ``_default_config_path`` this never falls back to the repo
    ``config/homemaster.yaml``: auth always provisions the per-user home
    file, creating ``$HOMEMASTER_HOME`` when needed.
    """

    if config_path is not None:
        return Path(config_path).expanduser()
    values = os.environ if environ is None else environ
    return _homemaster_home(values) / "config.yaml"


def build_provider_item(answers: ProviderAnswers) -> dict[str, Any]:
    """Render one ``providers.items`` entry in the example.yaml field order."""

    # ``transport`` is intentionally not written: the field is deprecated
    # (substrate no longer selects a transport) and persisting it would
    # trigger the load-time DeprecationWarning on every product-written file.
    item: dict[str, Any] = {
        "name": answers.name,
        "kind": "chat",
        "api_format": answers.api_format,
        "auth_type": answers.auth_type,
        "base_url": answers.base_url,
        "model": answers.model,
        "api_keys": [answers.api_key] if answers.api_key else [],
        "context_window_tokens": answers.context_window_tokens,
        "max_output_tokens": None,
    }
    return item


def merge_providers_section(
    payload: Mapping[str, Any],
    answers: ProviderAnswers,
) -> dict[str, Any]:
    """Return *payload* with only the providers wiring updated.

    The named item is replaced in place (case-insensitive name match) or
    appended; ``providers.default`` and ``runtime_defaults.default_provider_name``
    move only when ``answers.set_default`` is true.  All other keys keep
    their values.
    """

    merged = dict(payload)
    raw_providers = merged.get("providers")
    providers: dict[str, Any] = dict(raw_providers) if isinstance(raw_providers, Mapping) else {}
    raw_items = providers.get("items")
    items: list[Any] = list(raw_items) if isinstance(raw_items, list) else []

    new_item = build_provider_item(answers)
    replaced = False
    for index, existing in enumerate(items):
        if (
            isinstance(existing, Mapping)
            and str(existing.get("name", "")).strip().casefold() == answers.name.casefold()
        ):
            items[index] = new_item
            replaced = True
            break
    if not replaced:
        items.append(new_item)
    providers["items"] = items

    if answers.set_default:
        providers["default"] = answers.name
        raw_defaults = merged.get("runtime_defaults")
        runtime_defaults = dict(raw_defaults) if isinstance(raw_defaults, Mapping) else {}
        runtime_defaults["default_provider_name"] = answers.name
        merged["runtime_defaults"] = runtime_defaults
    # When the user declines, ``providers.default`` and ``runtime_defaults``
    # stay exactly as they were (present or absent) — only items changed.

    merged["providers"] = providers
    return merged


def minimal_config_payload(answers: ProviderAnswers) -> dict[str, Any]:
    """Smallest complete config for a fresh ``config.yaml``.

    Every other ``HomeMasterConfig`` section defaults in code; the files
    memory tier is explicit so the install works without the ``memory``
    extra.  ``runtime_defaults`` is written unconditionally so the runtime
    resolves the new provider even when it is not named ``Mimo``.
    """

    return {
        "providers": {
            "default": answers.name,
            "items": [build_provider_item(answers)],
        },
        "runtime_defaults": {
            "default_provider_name": answers.name,
        },
        "memory": {
            "mode": "files",
        },
    }


def write_auth_config(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically write *payload* as YAML with owner-only permissions."""

    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        dict(payload),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", dir=str(path.parent), suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        tmp_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def env_override_hint(provider_name: str) -> str:
    """Name of the env var that can supply this provider's API key."""

    normalized = "".join(ch if ch.isalnum() else "_" for ch in provider_name.upper())
    return f"HOMEMASTER_{normalized}_API_KEY"


def _load_existing_payload(target: Path) -> dict[str, Any] | None:
    """Return the existing YAML mapping, or None when the file is absent."""

    if not target.is_file():
        return None
    try:
        payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid HomeMaster YAML config: {target}") from exc
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ConfigError(f"HomeMaster config must be a YAML mapping: {target}")
    return payload


def _existing_provider_names(payload: Mapping[str, Any] | None) -> set[str]:
    if not isinstance(payload, Mapping):
        return set()
    providers = payload.get("providers")
    if not isinstance(providers, Mapping):
        return set()
    items = providers.get("items")
    if not isinstance(items, list):
        return set()
    return {
        str(item.get("name")).strip()
        for item in items
        if isinstance(item, Mapping) and str(item.get("name", "")).strip()
    }


# ---------------------------------------------------------------------------
# ProviderConfigService — validate → write → hot-apply for web/CLI callers
# ---------------------------------------------------------------------------

# Payload fields accepted by the write endpoints.  ``extra="forbid"`` on
# ProviderProfileConfig means anything else must be rejected here first.
_PROVIDER_PAYLOAD_FIELDS = frozenset(
    {
        "name",
        "template",
        "api_format",
        "transport",
        "auth_type",
        "base_url",
        "model",
        "api_key",
        "api_keys",
        "context_window_tokens",
        "max_output_tokens",
        "thinking_enable",
        "thinking_budget",
        "embedding_url",
        "kind",
        "set_default",
    }
)
_PATCH_FIELDS = _PROVIDER_PAYLOAD_FIELDS - {"template"}
# ``ProviderAnswers`` is the wizard's shape; these ProviderProfileConfig
# fields ride along on the merged item without an answers slot.
_ITEM_PASSTHROUGH_FIELDS = (
    "kind",
    "api_keys",
    "max_output_tokens",
    "thinking_enable",
    "thinking_budget",
    "embedding_url",
)
_ITEM_SCALAR_FIELDS = (
    "api_format",
    "transport",
    "auth_type",
    "base_url",
    "model",
    "kind",
    "context_window_tokens",
    "max_output_tokens",
    "thinking_enable",
    "thinking_budget",
    "embedding_url",
)
_MODELS_TIMEOUT_S = 15.0
_PROBE_TIMEOUT_CAP_S = 30.0


class ProviderConfigError(RuntimeError):
    """Provider-management failure carrying its HTTP mapping."""

    status_code = 500
    code = "provider_config_error"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable


class ProviderValidationError(ProviderConfigError):
    """Payload failed field-level or whole-document validation (HTTP 422)."""

    status_code = 422
    code = "invalid_provider"


class ProviderNotFoundError(ProviderConfigError):
    """The named provider is not configured (HTTP 404)."""

    status_code = 404
    code = "provider_not_found"


class ProviderConflictError(ProviderConfigError):
    """The write conflicts with existing provider state (HTTP 409)."""

    status_code = 409
    code = "provider_conflict"


class ProviderConfigService:
    """Provider profile CRUD + connectivity probes bound to one config file.

    ``config_path`` falls back to ``runtime_config.config_path`` and finally
    to the auth default (``$HOMEMASTER_HOME/config.yaml``).
    ``runtime_config`` — normally the ``HomeMasterConfig`` composed into the
    running application — is hot-applied on every successful write; ``None``
    (CLI-style use) skips runtime application.  ``probe`` replaces the live
    connectivity check inside :meth:`test` so callers/tests can exercise the
    error-kind classification without network access.
    """

    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        runtime_config: Any | None = None,
        probe: Callable[[ProviderProfileConfig], Any] | None = None,
    ) -> None:
        resolved = config_path
        if resolved is None and runtime_config is not None:
            resolved = getattr(runtime_config, "config_path", None)
        self._config_path = (
            Path(resolved).expanduser()
            if resolved is not None
            else resolve_auth_config_path()
        )
        self._runtime_config = runtime_config
        self._probe = probe

    @property
    def config_path(self) -> Path:
        return self._config_path

    # -- read views --------------------------------------------------------

    def templates(self) -> tuple[ProviderTemplate, ...]:
        return provider_templates()

    def list_profiles(self) -> list[dict[str, Any]]:
        """Masked provider views — key material is never echoed."""

        return [_profile_summary(item) for item in self._current_items()]

    # -- writes ------------------------------------------------------------

    def upsert(
        self, payload: Mapping[str, Any], *, overwrite: bool = False
    ) -> dict[str, Any]:
        """Create or replace one provider profile; apply it to the runtime."""

        existing = self._read_existing()
        answers, extras = self._payload_to_answers(
            payload, existing_names=_existing_provider_names(existing)
        )
        item = build_provider_item(answers)
        item.update(extras)
        profile = self._validate_item(item)
        prior = _find_item(_provider_items(existing), profile.name)
        status = "created"
        if prior is not None:
            identical = _same_item(prior, item)
            if not overwrite and not identical:
                raise ProviderConflictError(
                    f"provider {profile.name!r} already exists with different "
                    "settings; retry with ?overwrite=true to replace it"
                )
            status = "unchanged" if identical else "updated"
        merged = merge_providers_section(existing, answers)
        _replace_merged_item(merged, item)
        validated = self._validate_document(merged)
        self._write_candidate(merged)
        self._apply_to_runtime(validated)
        return {
            "provider": _profile_summary(profile),
            "status": status,
            "default": validated.providers.default,
        }

    def patch(self, name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Partial update of one stored profile; ``api_key: ""`` keeps keys."""

        if not isinstance(payload, Mapping):
            raise ProviderValidationError("provider patch must be a JSON object")
        unknown = sorted(set(payload) - _PATCH_FIELDS)
        if unknown:
            raise ProviderValidationError(
                f"unknown provider fields: {', '.join(unknown)}"
            )
        if "name" in payload and str(payload["name"]).strip().casefold() != name.casefold():
            raise ProviderValidationError("provider name is fixed by the request path")
        existing = self._read_existing()
        providers = _mutable_section(existing, "providers")
        items = _provider_items({"providers": providers})
        index = _item_index(items, name)
        if index is None:
            raise ProviderNotFoundError(f"provider {name!r} is not configured")
        raw = items[index]
        item = _item_to_dict(raw)
        for field in _ITEM_SCALAR_FIELDS:
            if field in payload:
                item[field] = payload[field]
        if "api_key" in payload:
            key = str(payload["api_key"]).strip()
            if key:
                item["api_keys"] = [key]
            # "" = leave the stored keys untouched.
        if "api_keys" in payload:
            item["api_keys"] = [str(value) for value in payload["api_keys"] or ()]
        profile = self._validate_item(item)
        items[index] = item
        providers["items"] = items
        candidate = dict(existing)
        candidate["providers"] = providers
        if payload.get("set_default"):
            if profile.kind != "chat":
                raise ProviderValidationError(
                    f"only chat providers can be the default; {name!r} is "
                    f"kind={profile.kind!r}"
                )
            providers["default"] = profile.name
            runtime_defaults = _mutable_section(existing, "runtime_defaults")
            runtime_defaults["default_provider_name"] = profile.name
            candidate["runtime_defaults"] = runtime_defaults
        validated = self._validate_document(candidate)
        self._write_candidate(candidate)
        self._apply_to_runtime(validated)
        return {
            "provider": _profile_summary(profile),
            "status": "updated",
            "default": validated.providers.default,
        }

    def delete(self, name: str) -> dict[str, Any]:
        """Remove one profile; reassigns a dangling default to the first chat item."""

        existing = self._read_existing()
        providers = _mutable_section(existing, "providers")
        items = _provider_items({"providers": providers})
        index = _item_index(items, name)
        if index is None:
            raise ProviderNotFoundError(f"provider {name!r} is not configured")
        items.pop(index)
        providers["items"] = items
        candidate = dict(existing)
        candidate["providers"] = providers
        folded = name.casefold()
        default_dangling = (
            str(providers.get("default") or "").strip().casefold() == folded
        )
        runtime_defaults = _mutable_section(existing, "runtime_defaults")
        runtime_dangling = (
            str(runtime_defaults.get("default_provider_name") or "")
            .strip()
            .casefold()
            == folded
        )
        if default_dangling or runtime_dangling:
            replacement = next(
                (
                    str(item.get("name")).strip()
                    for item in items
                    if isinstance(item, Mapping)
                    and str(item.get("kind") or "chat") == "chat"
                    and str(item.get("name") or "").strip()
                ),
                None,
            )
            if replacement is None:
                raise ProviderConflictError(
                    f"deleting {name!r} leaves no chat provider to take over "
                    "as the default; configure another provider first"
                )
            if default_dangling:
                providers["default"] = replacement
            if runtime_dangling:
                runtime_defaults["default_provider_name"] = replacement
                candidate["runtime_defaults"] = runtime_defaults
        validated = self._validate_document(candidate)
        self._write_candidate(candidate)
        self._apply_to_runtime(validated)
        return {
            "deleted": True,
            "name": name,
            "default": validated.providers.default,
        }

    # -- connectivity probes -------------------------------------------------

    async def test(self, target: Any) -> dict[str, Any]:
        """Probe one stored (``{"name": ...}``) or unsaved (full fields) profile.

        Never raises for probe failures: the outcome is classified into
        ``error_kind`` — ``auth``/``network``/``model``/``schema``/``unknown``
        — so onboarding can react without parsing transport errors.
        """

        started = time.perf_counter()
        try:
            profile = self._resolve_test_profile(target)
        except ProviderConfigError as exc:
            requested = (
                target
                if isinstance(target, str)
                else (target.get("name") if isinstance(target, Mapping) else None)
            )
            return {
                "ok": False,
                "error_kind": "unknown",
                "message": str(exc),
                "latency_ms": _elapsed_ms(started),
                "name": str(requested) if requested else None,
                "model": None,
            }
        try:
            if self._probe is not None:
                outcome = self._probe(profile)
                if inspect.isawaitable(outcome):
                    outcome = await outcome
            else:
                outcome = await self._default_probe(profile)
        except Exception as exc:
            return {
                "ok": False,
                "error_kind": classify_error_kind(exc),
                "message": _redact_keys(str(getattr(exc, "message", "") or exc), profile),
                "latency_ms": _elapsed_ms(started),
                "name": profile.name,
                "model": profile.model,
            }
        result = dict(outcome) if isinstance(outcome, Mapping) else {"ok": bool(outcome)}
        ok = bool(result.get("ok"))
        raw_message = str(
            result.get("message") or ("provider probe succeeded" if ok else "provider probe failed")
        )
        return {
            "ok": ok,
            "error_kind": None if ok else result.get("error_kind") or "unknown",
            "message": _redact_keys(raw_message, profile),
            "latency_ms": _elapsed_ms(started),
            "name": profile.name,
            "model": profile.model,
        }

    async def list_models(self, name: str) -> dict[str, Any]:
        """Live ``GET {base_url}/models`` enumeration with a static fallback."""

        profile = self._find_profile(name)
        if profile is None:
            raise ProviderNotFoundError(f"provider {name!r} is not configured")
        try:
            ids = await self._fetch_model_ids(profile)
        except Exception:
            logger.info("provider model enumeration failed for %r", name, exc_info=True)
            ids = None
        if ids:
            return {"name": profile.name, "models": ids, "source": "live"}
        return {"name": profile.name, "models": _fallback_models(profile), "source": "fallback"}

    # -- internals -----------------------------------------------------------

    def _read_existing(self) -> dict[str, Any]:
        try:
            return _load_existing_payload(self._config_path) or {}
        except ConfigError as exc:
            raise ProviderConfigError(
                f"cannot read {self._config_path}: {exc}",
                status_code=503,
                code="config_unreadable",
                retryable=True,
            ) from exc

    def _current_items(self) -> list[Any]:
        runtime = self._runtime_config
        if runtime is not None:
            items = getattr(getattr(runtime, "providers", None), "items", None)
            if items is not None:
                return list(items)
        return _provider_items(self._read_existing())

    def _payload_to_answers(
        self,
        payload: Mapping[str, Any],
        *,
        existing_names: set[str],
    ) -> tuple[ProviderAnswers, dict[str, Any]]:
        """Adapt an API payload onto the wizard-shaped ``ProviderAnswers``.

        Fields the wizard cannot express (``kind``, ``embedding_url`` …) ride
        along in the returned extras dict instead of being force-fit.
        """

        if not isinstance(payload, Mapping):
            raise ProviderValidationError("provider payload must be a JSON object")
        unknown = sorted(set(payload) - _PROVIDER_PAYLOAD_FIELDS)
        if unknown:
            raise ProviderValidationError(
                f"unknown provider fields: {', '.join(unknown)}"
            )
        template = None
        template_key = str(payload.get("template") or "").strip()
        if template_key:
            template = next(
                (item for item in provider_templates() if item.key == template_key),
                None,
            )
            if template is None:
                raise ProviderValidationError(
                    f"unknown provider template {template_key!r}; choose one of "
                    + ", ".join(item.key for item in provider_templates())
                )
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ProviderValidationError("provider name is required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            raise ProviderValidationError(
                "provider name must start alphanumeric and contain only "
                "letters, digits, '.', '_' or '-' (URL-unsafe names would be "
                "unreachable via /api/providers/{name})"
            )
        api_format = str(
            payload.get("api_format") or (template.api_format if template else "")
        ).strip()
        if not api_format:
            raise ProviderValidationError("api_format is required when no template is given")
        transport = str(
            payload.get("transport")
            or (template.transport if template else "")
            or ("anthropic_sdk" if api_format in ANTHROPIC_WIRE_FORMATS else "openai_sdk")
        ).strip()
        auth_type = str(payload.get("auth_type") or "api_key").strip()
        base_url = str(
            payload.get("base_url") or (template.base_url if template else "")
        ).strip()
        model = str(payload.get("model") or (template.model if template else "")).strip()
        if not base_url:
            raise ProviderValidationError("base_url is required")
        if not model:
            raise ProviderValidationError("model is required")
        api_key = str(payload.get("api_key") or "").strip()
        if not api_key:
            raw_keys = payload.get("api_keys")
            if isinstance(raw_keys, (list, tuple)) and raw_keys:
                api_key = str(raw_keys[0]).strip()
        try:
            context_window_tokens = int(
                payload.get("context_window_tokens") or DEFAULT_CONTEXT_WINDOW_TOKENS
            )
        except (TypeError, ValueError) as exc:
            raise ProviderValidationError("context_window_tokens must be an integer") from exc
        raw_default = payload.get("set_default")
        # Mirror the wizard: the first configured provider becomes the default
        # unless the caller explicitly declines. Only chat providers may be
        # the default — an embedding profile as chat default breaks every run.
        kind = str(payload.get("kind") or "chat").strip() or "chat"
        if raw_default is not None:
            set_default = bool(raw_default)
        else:
            set_default = not existing_names and kind == "chat"
        if set_default and kind != "chat":
            raise ProviderValidationError(
                f"only chat providers can be the default; {name!r} is kind={kind!r}"
            )
        answers = ProviderAnswers(
            name=name,
            api_format=api_format,
            transport=transport,
            auth_type=auth_type,
            base_url=base_url,
            model=model,
            api_key=api_key,
            context_window_tokens=context_window_tokens,
            set_default=set_default,
        )
        extras: dict[str, Any] = {}
        for field in _ITEM_PASSTHROUGH_FIELDS:
            if field == "api_keys":
                if "api_keys" in payload:
                    raw = payload["api_keys"]
                    if not isinstance(raw, (list, tuple)):
                        raise ProviderValidationError("api_keys must be a list of strings")
                    extras["api_keys"] = [str(value) for value in raw]
                continue
            if field in payload:
                extras[field] = payload[field]
        return answers, extras

    def _resolve_test_profile(self, target: Any) -> ProviderProfileConfig:
        """``{name}`` resolves a stored profile; a fuller payload builds an ephemeral one."""

        if isinstance(target, str):
            target = {"name": target}
        if not isinstance(target, Mapping):
            raise ProviderValidationError(
                "provider test payload must be a name or a profile object"
            )
        unknown = sorted(set(target) - _PROVIDER_PAYLOAD_FIELDS)
        if unknown:
            raise ProviderValidationError(
                f"unknown provider fields: {', '.join(unknown)}"
            )
        name = str(target.get("name") or "").strip()
        overrides = {
            key: value
            for key, value in target.items()
            if key not in {"name", "template", "set_default"}
        }
        if not overrides:
            if not name:
                raise ProviderValidationError(
                    "provider test requires a name or a full profile payload"
                )
            profile = self._find_profile(name)
            if profile is None:
                raise ProviderNotFoundError(f"provider {name!r} is not configured")
            return profile
        base_item: dict[str, Any] | None = None
        stored_item: dict[str, Any] | None = None
        if name:
            raw = _find_item(self._current_items(), name)
            if raw is not None:
                stored_item = _item_to_dict(raw)
                base_item = dict(stored_item)
        if base_item is None:
            answers, extras = self._payload_to_answers(
                {**target, "name": name or "preview", "set_default": False},
                existing_names=set(),
            )
            base_item = build_provider_item(answers)
            base_item.update(extras)
        if "api_key" in overrides:
            key = str(overrides.pop("api_key") or "").strip()
            if key:
                base_item["api_keys"] = [key]
        for field, value in overrides.items():
            base_item[field] = value
        # Stored credentials must never be re-targeted at a caller-chosen
        # endpoint: if the probe reroutes the request (base_url) or reshapes
        # auth (api_format/auth_type) away from the stored profile, drop the
        # stored keys unless the caller supplied an explicit api_key.
        if stored_item is not None and "api_key" not in target:
            rerouted = any(
                field in overrides
                and str(overrides[field] or "")
                != str(stored_item.get(field) or "")
                for field in ("base_url", "api_format", "auth_type")
            )
            if rerouted:
                base_item["api_keys"] = []
        return self._validate_item(base_item)

    def _find_profile(self, name: str) -> ProviderProfileConfig | None:
        folded = name.strip().casefold()
        for item in self._current_items():
            profile = _item_to_profile(item)
            if profile is not None and profile.name.casefold() == folded:
                return profile
        return None

    def _validate_item(self, item: Mapping[str, Any]) -> ProviderProfileConfig:
        try:
            return ProviderProfileConfig.model_validate(dict(item))
        except ValidationError as exc:
            raise ProviderValidationError(_validation_details(exc)) from exc

    def _validate_document(self, payload: Mapping[str, Any]) -> HomeMasterConfig:
        try:
            return HomeMasterConfig.model_validate(dict(payload))
        except ValidationError as exc:
            raise ProviderValidationError(_validation_details(exc)) from exc

    def _write_candidate(self, payload: Mapping[str, Any]) -> None:
        try:
            write_auth_config(self._config_path, payload)
        except OSError as exc:
            raise ProviderConfigError(
                f"cannot write {self._config_path}: {exc}",
                status_code=503,
                code="config_write_failed",
                retryable=True,
            ) from exc

    def _apply_to_runtime(self, validated: HomeMasterConfig) -> None:
        """Hot-apply the validated ``providers`` section onto the live config.

        Env overrides are re-applied (same as ``load_config``) so profiles
        whose keys live only in ``HOMEMASTER_<NAME>_*`` keep working after a
        hot apply.  Only the providers section is replaced; every other
        section stays restart-scoped.
        """

        runtime = self._runtime_config
        if runtime is None or getattr(runtime, "providers", None) is None:
            return
        effective = _apply_env_overrides(validated)
        try:
            runtime.providers = effective.providers
            # Provider writes can also move the default pointer
            # (set_default / delete-reassign); keep the live value in sync
            # or default-provider runs resolve a stale name until restart.
            if getattr(runtime, "runtime_defaults", None) is not None:
                runtime.runtime_defaults = effective.runtime_defaults
        except Exception:
            logger.warning("provider runtime hot-apply failed", exc_info=True)

    async def _default_probe(self, profile: ProviderProfileConfig) -> dict[str, Any]:
        """Pure-async sibling of doctor's ``_live_mimo_smoke``/embedding smoke."""

        configured = getattr(
            getattr(self._runtime_config, "provider_client", None), "timeout_s", None
        )
        timeout_s = min(float(configured or _PROBE_TIMEOUT_CAP_S), _PROBE_TIMEOUT_CAP_S)
        if profile.kind == "embedding":
            from homemaster.providers.embedding_client import BGEEmbeddingClient

            client = BGEEmbeddingClient(profile, timeout_s=timeout_s)
            try:
                response = await asyncio.to_thread(
                    client.embed_texts, ["HomeMaster embedding smoke"]
                )
            finally:
                await asyncio.to_thread(client.close)
            ok = bool(response.embeddings and response.embeddings[0])
            return {
                "ok": ok,
                "error_kind": None if ok else "schema",
                "message": (
                    "embedding provider returned a vector"
                    if ok
                    else "embedding provider returned an empty vector"
                ),
            }
        from homemaster.substrate.as_llm_client import AsLLMClient

        client = AsLLMClient(profile, timeout_s=timeout_s)
        try:
            response = await client.complete_json(
                '只输出 JSON object: {"ok": true}',
                temperature=0.0,
            )
        finally:
            await client.aclose()
        if response.json_payload.get("ok") is True:
            return {"ok": True, "message": "provider returned parseable JSON"}
        return {
            "ok": False,
            "error_kind": "schema",
            "message": "provider reply was JSON but did not contain ok:true",
        }

    async def _fetch_model_ids(self, profile: ProviderProfileConfig) -> list[str] | None:
        base_url = profile.base_url.rstrip("/")
        api_key = (profile.api_keys[0] if profile.api_keys else "") or os.getenv(
            env_override_hint(profile.name), ""
        )
        headers: dict[str, str] = {}
        if profile.api_format in ANTHROPIC_WIRE_FORMATS:
            url = f"{base_url}/v1/models"
            if api_key:
                if profile.auth_type == "auth_token":
                    headers["authorization"] = f"Bearer {api_key}"
                else:
                    headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            path = urlsplit(base_url).path.rstrip("/")
            suffix = "/models" if re.search(r"/v\d+[a-z0-9.]*$", path) else "/v1/models"
            url = f"{base_url}{suffix}"
            if api_key:
                headers["authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_MODELS_TIMEOUT_S), trust_env=False
        ) as client:
            response = await client.get(url, headers=headers)
        if response.status_code != 200:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        return _extract_model_ids(body) or None


def classify_error_kind(exc: BaseException) -> str:
    """Coarse taxonomy for ``test()`` failures.

    ``auth`` (401/403/credential errors), ``network`` (connect/DNS/timeout),
    ``model`` (404 / model-not-found), ``schema`` (unparseable provider
    response) and ``unknown`` for everything else.
    """

    from homemaster.providers.embedding_client import (
        EmbeddingClientError,
        EmbeddingProviderNetworkError,
    )
    from homemaster.providers.errors import (
        LLMAuthError,
        LLMClientError,
        LLMNetworkError,
        LLMProviderError,
    )

    status_code = _exception_status_code(exc)
    if isinstance(exc, LLMAuthError) or status_code in (401, 403):
        return "auth"
    if isinstance(exc, LLMClientError) and exc.cause_code == "no_keys":
        return "auth"
    if isinstance(exc, (LLMNetworkError, EmbeddingProviderNetworkError)):
        return "network"
    if isinstance(exc, httpx.RequestError):
        return "network"
    if status_code == 404:
        return "model"
    name = type(exc).__name__.lower()
    message = str(getattr(exc, "message", "") or exc)
    lowered = message.lower()
    if "authentication" in name or "permission" in name or "unauthorized" in lowered:
        return "auth"
    if isinstance(exc, LLMProviderError):
        if "not_json" in str(exc.error_type or ""):
            return "schema"
        if _looks_like_model_error(lowered):
            return "model"
        return "unknown"
    if isinstance(exc, EmbeddingClientError):
        http_match = re.search(r"http_(\d{3})", lowered)
        if http_match is not None:
            code = int(http_match.group(1))
            if code in (401, 403):
                return "auth"
            if code == 404:
                return "model"
        if "not_json" in str(exc.error_type or ""):
            return "schema"
        return "unknown"
    if "notfound" in name or _looks_like_model_error(lowered):
        return "model"
    if "timeout" in name or "connection" in name or "network" in name:
        return "network"
    return "unknown"


def _looks_like_model_error(message: str) -> bool:
    return "model" in message and (
        "not found" in message or "does not exist" in message or "404" in message
    )


def _exception_status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


# api_format values whose template is keyless (e.g. local Ollama): such a
# provider is usable with zero stored keys, so onboarding must count it.
_KEYLESS_API_FORMATS = frozenset(
    key for key, defaults in _TEMPLATE_DEFAULTS.items() if defaults.get("keyless")
)


def _profile_summary(item: Any) -> dict[str, Any]:
    """Masked view of one provider — key material never leaves the write path."""

    if isinstance(item, ProviderProfileConfig):
        api_keys = item.api_keys
        return {
            "name": item.name,
            "kind": item.kind,
            "model": item.model,
            "base_url": item.base_url,
            "api_format": item.api_format,
            "auth_type": item.auth_type,
            "api_key_configured": bool(api_keys),
            "api_key_count": len(api_keys),
            "keyless": item.api_format in _KEYLESS_API_FORMATS,
        }
    if isinstance(item, Mapping):
        raw_keys = item.get("api_keys")
        keys = list(raw_keys) if isinstance(raw_keys, (list, tuple)) else []
        api_format = str(item.get("api_format") or "")
        return {
            "name": str(item.get("name") or ""),
            "kind": str(item.get("kind") or "chat"),
            "model": str(item.get("model") or ""),
            "base_url": str(item.get("base_url") or ""),
            "api_format": api_format,
            "auth_type": str(item.get("auth_type") or "api_key"),
            "api_key_configured": bool(keys),
            "api_key_count": len(keys),
            "keyless": api_format in _KEYLESS_API_FORMATS,
        }
    return {
        "name": str(item),
        "kind": "chat",
        "model": "",
        "base_url": "",
        "api_format": "",
        "auth_type": "api_key",
        "api_key_configured": False,
        "api_key_count": 0,
        "keyless": False,
    }


def _provider_items(payload: Mapping[str, Any] | None) -> list[Any]:
    if not isinstance(payload, Mapping):
        return []
    providers = payload.get("providers")
    if not isinstance(providers, Mapping):
        return []
    items = providers.get("items")
    return list(items) if isinstance(items, list) else []


def _mutable_section(payload: Mapping[str, Any], key: str) -> dict[str, Any]:
    section = payload.get(key)
    return dict(section) if isinstance(section, Mapping) else {}


def _item_index(items: list[Any], name: str) -> int | None:
    folded = name.strip().casefold()
    for index, item in enumerate(items):
        item_name = item.name if isinstance(item, ProviderProfileConfig) else None
        if item_name is None and isinstance(item, Mapping):
            item_name = str(item.get("name") or "")
        if item_name is not None and str(item_name).strip().casefold() == folded:
            return index
    return None


def _find_item(items: list[Any], name: str) -> Any | None:
    index = _item_index(items, name)
    return items[index] if index is not None else None


def _item_to_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, ProviderProfileConfig):
        return item.model_dump(mode="json")
    if isinstance(item, Mapping):
        return dict(item)
    return {}


def _item_to_profile(item: Any) -> ProviderProfileConfig | None:
    if isinstance(item, ProviderProfileConfig):
        return item
    if isinstance(item, Mapping):
        try:
            return ProviderProfileConfig.model_validate(dict(item))
        except ValidationError:
            return None
    return None


def _same_item(existing: Any, new_item: Mapping[str, Any]) -> bool:
    try:
        before = ProviderProfileConfig.model_validate(dict(_item_to_dict(existing)))
        after = ProviderProfileConfig.model_validate(dict(new_item))
    except ValidationError:
        return False
    return before.model_dump(mode="json") == after.model_dump(mode="json")


def _replace_merged_item(merged: dict[str, Any], item: Mapping[str, Any]) -> None:
    """Swap the wizard-shaped merged item for the extras-augmented one."""

    providers = merged.get("providers")
    if not isinstance(providers, dict):
        return
    items = providers.get("items")
    if not isinstance(items, list):
        return
    index = _item_index(items, str(item.get("name") or ""))
    if index is not None:
        items[index] = dict(item)


def _fallback_models(profile: ProviderProfileConfig) -> list[str]:
    models = [profile.model]
    for template in provider_templates():
        if template.api_format == profile.api_format and template.model not in models:
            if template.model:
                models.append(template.model)
    return models


def _extract_model_ids(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return []
    data = body.get("data")
    ids: list[str] = []
    if isinstance(data, list):
        ids.extend(
            str(item["id"])
            for item in data
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        )
    models = body.get("models")  # Ollama native listing shape.
    if isinstance(models, list):
        ids.extend(
            str(item.get("model") or item.get("name"))
            for item in models
            if isinstance(item, dict)
            and str(item.get("model") or item.get("name") or "").strip()
        )
    return [value for value in ids if value]


def _validation_details(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors(include_input=False)
    )


def _redact_keys(message: str, profile: ProviderProfileConfig | None) -> str:
    """Never echo key material inside a probe failure message.

    Probes may authenticate with an env-sourced key (``HOMEMASTER_<NAME>_*``)
    that is not part of the stored profile — scrub that too.
    """

    candidates: list[str] = []
    if profile is not None:
        candidates.extend(profile.api_keys)
        env_key = os.getenv(env_override_hint(profile.name), "")
        if env_key:
            candidates.append(env_key)
    for key in candidates:
        if len(key) >= 4 and key in message:
            message = message.replace(key, "***")
    return message[:500]


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


__all__ = [
    "ProviderAnswers",
    "ProviderConfigError",
    "ProviderConfigService",
    "ProviderConflictError",
    "ProviderNotFoundError",
    "ProviderTemplate",
    "ProviderValidationError",
    "build_provider_item",
    "classify_error_kind",
    "env_override_hint",
    "mask_api_key",
    "merge_providers_section",
    "minimal_config_payload",
    "provider_templates",
    "resolve_auth_config_path",
    "write_auth_config",
]
