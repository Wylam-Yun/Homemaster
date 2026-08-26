"""Definition for browser_network."""

from homemaster.tools.browser._common import ExecutionProof, object_schema, registered


def build(session: object):
    schema = object_schema(
        {
            "mode": {
                "type": "string",
                "enum": ["list", "detail"],
                "description": "Preview list or one request detail.",
            },
            "request_ref": {"type": "string", "description": "Stable ref returned by list."},
            "failed_only": {"type": "boolean", "description": "Only failed responses."},
            "include_static": {"type": "boolean", "description": "Include static resources."},
            "fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Bounded response fields to include in list previews.",
            },
            "resource_type": {
                "type": "string",
                "description": "Optional browser resource-type filter such as xhr or fetch.",
            },
            "since_ms": {"type": "integer", "minimum": 0, "description": "Time lower bound."},
            "until_ms": {"type": "integer", "minimum": 0, "description": "Time upper bound."},
            "cursor": {"type": "string", "description": "Bounded continuation cursor."},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "description": "Record limit.",
            },
            "max_body_chars": {
                "type": "integer",
                "minimum": 1,
                "description": "Response body bound.",
            },
        },
        ("mode",),
    )
    schema["allOf"] = [
        {
            "if": {"properties": {"mode": {"const": "detail"}}},
            "then": {"required": ["request_ref"]},
        }
    ]
    return registered(
        session=session,
        name="browser_network",
        operation="network",
        effects=("read",),
        capabilities=("device.read",),
        proof=ExecutionProof.NONE,
        description=(
            "Capture browser network requests as bounded shape previews and retrieve one authorized "
            "response body by stable request ref. Use `mode=list` to inspect URLs, methods, status, "
            "timing, resource type, and JSON shape; use `mode=detail` only after selecting a returned "
            "request ref. Filter by fields, failure status, resource type, or time window, and "
            "continue with a cursor instead of an unbounded live stream. This tool observes traffic "
            "only and cannot send, replay, or modify a request."
        ),
        schema=schema,
    )
