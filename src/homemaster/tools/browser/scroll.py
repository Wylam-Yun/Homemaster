"""Definition for browser_scroll."""

from homemaster.tools.browser._common import (
    TARGET_SCHEMA,
    ExecutionProof,
    object_schema,
    registered,
)


def build(session: object):
    schema = object_schema(
        {
            "mode": {
                "type": "string",
                "enum": ["by", "into_view", "auto"],
                "description": "Scroll mode.",
            },
            "container": TARGET_SCHEMA,
            "target": TARGET_SCHEMA,
            "direction": {
                "type": "string",
                "enum": ["up", "down", "left", "right"],
                "description": "Direction for by/auto.",
            },
            "amount_px": {"type": "integer", "minimum": 1, "description": "Pixels per step."},
            "steps": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "Bounded auto-scroll steps.",
            },
            "delay_ms": {
                "type": "integer",
                "minimum": 0,
                "description": "Delay between auto-scroll steps.",
            },
        },
        ("mode",),
    )
    schema["allOf"] = [
        {
            "if": {"properties": {"mode": {"const": "into_view"}}},
            "then": {"required": ["target"]},
        }
    ]
    return registered(
        session=session,
        name="browser_scroll",
        operation="scroll",
        effects=("browser.interact",),
        capabilities=("device.control",),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        observe=False,
        description=(
            "Scroll the page or one scrollable container, bring one target into view, or perform a "
            "bounded auto-scroll for lazy-loaded content. Use `mode=by` with direction and pixels, "
            "`mode=into_view` with a target, or `mode=auto` with a bounded step count. Success "
            "reports before/after scroll positions or verified target visibility; reaching a "
            "scroll boundary returns `changed=false`."
        ),
        schema=schema,
    )
