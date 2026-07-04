"""Event trace sanitization — delegates to events.trace.sanitize_for_log for redaction.

Adds truncation for large payloads and raw prompt/response content.
Secret-pattern redaction is handled by events.trace.sanitize_for_log (handles
dicts, lists, tuples, substring matching on key names).
"""

from __future__ import annotations

from typing import Any

from homemaster.events.trace import sanitize_for_log

_MAX_PAYLOAD_LEN = 4000
_MAX_PROMPT_VALUE_LEN = 200

# Keys whose string values are truncated to _MAX_PROMPT_VALUE_LEN
# to prevent raw prompt/response content from entering the default trace.
_PROMPT_KEYS = frozenset({"prompt", "response", "content", "message"})


def sanitize_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Redact secrets via trace.sanitize_for_log, then truncate large values."""
    sanitized: dict[str, Any] = sanitize_for_log(payload)
    sanitized = _truncate_prompt_values(sanitized)
    serialized = str(sanitized)
    if len(serialized) > _MAX_PAYLOAD_LEN:
        sanitized["_truncated"] = True
        sanitized["_truncated_len"] = len(serialized)
    return sanitized


def sanitize_for_trace(payload: dict[str, Any]) -> dict[str, Any]:
    """Redact secrets for debug traces without truncating payload content."""

    return sanitize_for_log(payload)


def _truncate_prompt_values(d: dict[str, Any]) -> dict[str, Any]:
    """Truncate long string values under prompt-like keys."""
    result: dict[str, Any] = {}
    for key, value in d.items():
        if isinstance(value, str) and len(value) > _MAX_PROMPT_VALUE_LEN:
            if any(pk in key.lower() for pk in _PROMPT_KEYS):
                result[key] = f"[TRUNCATED: {_MAX_PROMPT_VALUE_LEN}/{len(value)} chars]"
            else:
                result[key] = value
        elif isinstance(value, dict):
            result[key] = _truncate_prompt_values(value)
        else:
            result[key] = value
    return result
