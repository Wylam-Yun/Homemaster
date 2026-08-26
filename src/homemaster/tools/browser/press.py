"""Definition for browser_press."""

from homemaster.tools.browser._common import (
    CONDITION_SCHEMA,
    ExecutionProof,
    registered,
    target_object,
)


def build(session: object):
    return registered(
        session=session,
        name="browser_press",
        operation="press",
        effects=("browser.interact",),
        capabilities=("device.control",),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        observe=False,
        description=(
            "Press one browser key or key combination such as Enter, Escape, Tab, ArrowDown, or "
            "Control+A. Provide a target when the shortcut must start from a specific element; "
            "otherwise it applies to the current focused element. Use `browser_type` for text and "
            "do not encode ordinary text as a sequence of key calls. Success reports focus and the "
            "requested observable page-state change."
        ),
        schema=target_object(
            {
                "key": {"type": "string", "description": "Key or Playwright key combination."},
                "modifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit modifiers.",
                },
                "expect": CONDITION_SCHEMA,
            },
            required=("key",),
        ),
    )
