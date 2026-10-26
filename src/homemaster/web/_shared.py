"""Helpers shared by the Web Console route modules.

Everything here was lifted out of ``app.py`` when the monolithic
``create_web_app`` factory was split into per-domain modules under
``homemaster.web.routes`` — the ``{code, message, retryable}`` error
envelope, session lookups, provider selection checks, and the
approval/grant projections are consumed by several domains.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse

from homemaster.application import RunRequest
from homemaster.services.provider_config import ProviderConfigError
from homemaster.tools.contracts import PermissionSubject

_DEFAULT_SUBJECT = RunRequest(text="Web permission subject").permission_subject
_WEB_PERMISSION_SUBJECT = PermissionSubject(
    subject_id="web-local-operator",
    channel="web",
    roles=_DEFAULT_SUBJECT.roles,
    tenant_id=_DEFAULT_SUBJECT.tenant_id,
    capabilities=tuple(
        capability for capability in _DEFAULT_SUBJECT.capabilities if capability != "tool.auto"
    ),
)

# Registry provenance sources mapped onto the browser-facing label set.
# ``bundled``/``explicit`` collapse onto labels the UI actually distinguishes.
_SKILL_SOURCE_LABELS = {
    "bundled": "builtin",
    "builtin": "builtin",
    "user": "user",
    "project": "project",
    "explicit": "user",
    "plugin": "plugin",
}


def _error(status_code: int, code: str, message: str, *, retryable: bool) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "retryable": retryable},
    )


_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\-一-鿿]+")


def _sanitize_filename(raw: str) -> str:
    leaf = Path(raw).name.strip()
    cleaned = _SAFE_FILENAME_RE.sub("_", leaf).strip("._")
    if not cleaned:
        return "attachment.bin"
    return cleaned[:120]


def _web_attachments_root(application: Any, *, create: bool = True) -> Path:
    working_dir = getattr(application, "working_directory", None)
    if isinstance(working_dir, Path):
        base = working_dir / "var" / "web_attachments"
    else:
        base = Path(tempfile.gettempdir()) / "homemaster_web_attachments"
    if create:
        base.mkdir(parents=True, exist_ok=True)
    return base.resolve()


def _session_attachment_dir(root: Path, session_id: str, *, create: bool = True) -> Path:
    safe_sid = _SAFE_FILENAME_RE.sub("_", session_id).strip("._") or "session"
    target = (root / safe_sid).resolve()
    target.relative_to(root)
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def _provider_config_error(exc: ProviderConfigError) -> JSONResponse:
    """Map a service-layer provider error onto the shared error envelope."""

    return _error(
        exc.status_code,
        exc.code,
        str(exc),
        retryable=exc.retryable,
    )


def _provider_selection_error(
    config: Any,
    *,
    provider_name: str | None,
    model: str | None,
) -> JSONResponse | None:
    """Mirror the factory's server-side provider/model resolution up front.

    ``model`` must match exactly one configured chat provider by name or
    model id, and must not conflict with an explicit ``provider_name``.
    """

    if provider_name is None and model is None:
        return None
    items = getattr(getattr(config, "providers", None), "items", None) or ()
    chat_providers = [provider for provider in items if getattr(provider, "kind", None) == "chat"]
    if provider_name is not None and not any(
        provider.name.casefold() == provider_name.casefold() for provider in chat_providers
    ):
        return _error(
            422,
            "unknown_provider",
            "The provider is not configured.",
            retryable=False,
        )
    if model is not None:
        target = model.casefold()
        matches = {
            provider.name.casefold(): provider
            for provider in chat_providers
            if provider.name.casefold() == target
            or str(getattr(provider, "model", "")).casefold() == target
        }
        if len(matches) > 1:
            return _error(
                422,
                "unknown_model",
                "The model must match exactly one configured chat provider.",
                retryable=False,
            )
        if len(matches) == 0:
            # With an explicit provider the model is a literal per-message
            # override; without one it must still select a provider.
            if provider_name is not None:
                return None
            return _error(
                422,
                "unknown_model",
                "The model must match exactly one configured chat provider.",
                retryable=False,
            )
        (matched,) = matches.values()
        if provider_name is not None and matched.name.casefold() != provider_name.casefold():
            return _error(
                422,
                "unknown_model",
                "The model conflicts with the selected provider.",
                retryable=False,
            )
    return None


def _application_services(application: Any) -> Mapping[str, Any]:
    services = getattr(getattr(application, "settings", None), "application_services", None)
    return services if isinstance(services, Mapping) else {}


def _session_ui_mode(application: Any, session_id: str) -> str:
    plan_mode = _application_services(application).get("plan_mode")
    enabled = getattr(plan_mode, "enabled", None)
    if callable(enabled) and enabled(session_id):
        return "plan"
    return "act"


def _status_to_dict(status: Any) -> dict[str, Any]:
    task_status = getattr(status, "task_status", None)
    return {
        "session_id": status.session_id,
        "generation": status.generation,
        "revision": status.revision,
        "status": status.status,
        "active": status.active,
        "cancellation_requested": status.cancellation_requested,
        "task_status": getattr(task_status, "value", task_status),
        "environment_ref": status.environment_ref,
    }


def _session_ids(manager: Any) -> set[str]:
    persisted = set(manager.list_session_ids())
    active = {runtime.session.session_id for runtime in manager.sessions}
    return persisted | active


async def _ensure_session_loaded(manager: Any, session_id: str) -> bool:
    """Materialize a persisted runtime so ``status``/``compact`` can read it."""

    try:
        manager.get(session_id)
        return True
    except KeyError:
        pass
    try:
        await manager.resume(session_id)
    except (KeyError, ValueError, FileNotFoundError):
        return False
    return True


def _message_text(message: Any) -> str:
    """Extract readable text from content blocks (UserMessage has no .text)."""

    content = getattr(message, "content", None)
    if isinstance(content, (list, tuple)):
        joined = "\n".join(str(getattr(block, "text", "") or "") for block in content)
        if joined.strip():
            return joined
    return str(getattr(message, "text", "") or "")


def _history_message(message: Any) -> dict[str, object]:
    projected: dict[str, object] = {
        "role": str(getattr(message, "role", "unknown")),
        "text": _message_text(message),
    }
    thinking = getattr(message, "reasoning_content", None)
    if isinstance(thinking, str) and thinking:
        projected["thinking"] = thinking
    tool_call_id = getattr(message, "tool_call_id", None)
    if isinstance(tool_call_id, str) and tool_call_id:
        projected["tool_call_id"] = tool_call_id
    name = getattr(message, "name", None)
    if isinstance(name, str) and name:
        projected["name"] = name
    return projected


def _resolution_to_dict(resolution: Any) -> dict[str, Any]:
    return {
        "approval_id": resolution.approval_id,
        "request_id": resolution.request_id,
        "request_status": resolution.request_status,
        "execution_started": resolution.execution_started,
        "persisted_grant_ids": list(resolution.persisted_grant_ids),
        "items": [{"item_id": item.item_id, "choice": item.choice} for item in resolution.items],
    }


def _approval_to_dict(stored: Any) -> dict[str, Any]:
    return {
        "approval_id": stored.approval_id,
        "request_id": stored.request_id,
        "environment_id": stored.environment_id,
        "revision": stored.revision,
        "intent_summary": stored.intent_summary,
        "request_status": stored.status,
        "created_at": stored.created_at,
        "deadline_at": stored.deadline_at,
        "resolved_at": stored.resolved_at,
        "items": [
            {
                "item_id": item.item_id,
                "display_name": item.display_name,
                "location": item.location,
                "action_label": item.action_label,
                "resource_kind": item.key.resource_kind,
                "resource_id": item.key.resource_id,
                "action": item.key.action,
                "decision": item.decision,
                "matched_grant_id": item.matched_grant_id,
                "step_ids": list(item.step_ids),
            }
            for item in stored.items
        ],
    }


def _grant_to_dict(grant: Any, display: tuple[str, str, str] | None = None) -> dict[str, Any]:
    return {
        "grant_id": grant.grant_id,
        "environment_id": grant.environment_id,
        "resource_kind": grant.resource_kind,
        "resource_id": grant.resource_id,
        "action": grant.action,
        "created_at": grant.created_at,
        "created_by": grant.created_by,
        "source_request_id": grant.source_request_id,
        "source_item_id": grant.source_item_id,
        "revoked_at": grant.revoked_at,
        "revoked_by": grant.revoked_by,
        "revision": grant.revision,
        "status": "active" if grant.revoked_at is None else "revoked",
        "display_name": display[0] if display is not None else None,
        "location": display[1] if display is not None else None,
        "action_label": display[2] if display is not None else None,
    }
