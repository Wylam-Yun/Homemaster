"""Event trace sanitization — basic implementation.

Phase 8 will add full PII redaction and structured sanitization rules.
This module provides basic secret-pattern redaction and payload truncation.
"""

from __future__ import annotations

import re
from typing import Any

_REDACT_PATTERNS = [
    re.compile(r"(api[_-]?key|token|secret|password|authorization)", re.IGNORECASE),
]

_MAX_PAYLOAD_LEN = 4000


def sanitize_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Redact potential secrets and truncate large payloads."""
    sanitized = _redact_dict(payload)
    serialized = str(sanitized)
    if len(serialized) > _MAX_PAYLOAD_LEN:
        sanitized["_truncated"] = True
    return sanitized


def _redact_dict(d: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key, value in d.items():
        if any(p.search(key) for p in _REDACT_PATTERNS):
            result[key] = "[REDACTED]"
        elif isinstance(value, dict):
            result[key] = _redact_dict(value)
        else:
            result[key] = value
    return result
