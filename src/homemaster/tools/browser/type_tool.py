"""Definition for browser_type."""

from homemaster.tools.browser._common import ExecutionProof, registered, target_object


def build(session: object):
    return registered(
        session=session,
        name="browser_type",
        operation="type",
        effects=("browser.dom_write",),
        capabilities=("device.control",),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        observe=False,
        description=(
            "Focus one editable target and enter text through real browser input events. Use "
            "`mode=replace` to select the current content before typing, matching OpenCLI `type`, or "
            "`mode=append` to keep the current value and add text at the active caret/end. Use "
            "`browser_fill` for deterministic exact assignment that does not require per-key "
            "behavior. Success reports the previous value, requested mode, inserted text, and "
            "verified final value."
        ),
        schema=target_object(
            {
                "text": {"type": "string", "description": "Text to enter."},
                "mode": {
                    "type": "string",
                    "enum": ["replace", "append"],
                    "description": "Replace or append input mode.",
                },
                "delay_ms": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Per-key delay in milliseconds.",
                },
            },
            required=("target", "text"),
        ),
    )
