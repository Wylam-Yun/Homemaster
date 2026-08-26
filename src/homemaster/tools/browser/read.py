"""Definition for browser_read."""

from homemaster.tools.browser._common import (
    TARGET_SCHEMA,
    ExecutionProof,
    object_schema,
    registered,
)


def build(session: object):
    schema = object_schema(
        {
            "kind": {
                "type": "string",
                "enum": ["title", "url", "text", "value", "attributes", "html", "form_state"],
                "description": "Property to read.",
            },
            "target": TARGET_SCHEMA,
            "frame_ref": {
                "type": "string",
                "description": "Optional frame identity returned by browser_inspect.",
            },
            "scope": TARGET_SCHEMA,
            "format": {
                "type": "string",
                "enum": ["html", "tree"],
                "description": "Raw bounded HTML or a structured HTML tree for kind=html.",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 1,
                "description": "Maximum characters returned for HTML or text.",
            },
            "max_depth": {
                "type": "integer",
                "minimum": 0,
                "maximum": 20,
                "description": "Maximum HTML tree depth when format=tree.",
            },
            "children_max": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "description": "Maximum children retained per HTML tree node.",
            },
            "text_max": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5000,
                "description": "Maximum text retained per HTML tree node.",
            },
        },
        ("kind",),
    )
    schema["allOf"] = [
        {
            "if": {
                "properties": {"kind": {"enum": ["text", "value", "attributes"]}}
            },
            "then": {"required": ["target"]},
        }
    ]
    return registered(
        session=session,
        name="browser_read",
        operation="read",
        effects=("read",),
        capabilities=("device.read",),
        proof=ExecutionProof.NONE,
        description=(
            "Get one page property or one resolved element value as structured data. Use "
            "`kind=title|url|text|value|attributes|html|form_state` when you need an exact readback "
            "for planning or verification rather than a broad snapshot. Use `browser_find` first "
            "when the target is unknown, and use `browser_extract` instead of raw HTML when the "
            "goal is to read a long article or document. This tool never changes the page and never "
            "treats the first of several action targets as uniquely identified."
        ),
        schema=schema,
    )
