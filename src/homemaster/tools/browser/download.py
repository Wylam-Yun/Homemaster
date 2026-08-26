"""Definition for browser_download."""

from homemaster.tools.browser._common import (
    TARGET_SCHEMA,
    ExecutionProof,
    object_schema,
    registered,
)


def build(session: object):
    return registered(
        session=session,
        name="browser_download",
        operation="download",
        effects=("browser.download",),
        capabilities=("device.control",),
        proof=ExecutionProof.EXTERNAL_STATE,
        observe=False,
        description=(
            "Arm browser download observation, perform one exact trigger action, and persist the "
            "completed download as an approved artifact. Use this when HomeMaster must initiate and "
            "collect a download atomically. Use `browser_wait(condition=download)` only to wait for "
            "a download already initiated by another allowed action. Success requires a completed "
            "browser download plus artifact existence, filename, size, SHA-256, and browser return "
            "state."
        ),
        schema=object_schema(
            {
                "trigger": TARGET_SCHEMA,
                "pattern": {"type": "string", "description": "Optional filename or URL pattern."},
                "timeout_ms": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Bounded download timeout.",
                },
            },
            ("trigger",),
        ),
    )
