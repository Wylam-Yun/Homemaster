"""Definition for browser_find."""

from homemaster.tools.browser._common import ExecutionProof, object_schema, registered


def build(session: object):
    props = {
        "role": {"type": "string", "description": "ARIA/native role."},
        "name": {"type": "string", "description": "Accessible name."},
        "label": {"type": "string", "description": "Associated label."},
        "text": {"type": "string", "description": "Visible text."},
        "testid": {"type": "string", "description": "Allowlisted test id."},
        "css": {
            "type": "string",
            "description": "Read-only CSS selector used only to return candidates.",
        },
        "match": {
            "type": "string",
            "enum": ["exact", "contains", "regex"],
            "description": "Exact, contains, or read-only regex.",
        },
        "nth": {"type": "integer", "minimum": 0, "description": "Explicit result index."},
        "frame_ref": {"type": "string", "description": "Frame identity."},
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
            "description": "Candidate limit.",
        },
        "text_max": {"type": "integer", "minimum": 1, "description": "Visible text bound."},
    }
    return registered(
        session=session,
        name="browser_find",
        operation="find",
        effects=("read",),
        capabilities=("device.read",),
        proof=ExecutionProof.NONE,
        description=(
            "Find DOM elements by semantic role, accessible name, associated label, visible text, "
            "test id, or a read-only CSS selector and return structured candidates with stable "
            "target refs. Use this when you know what kind of element you need but do not yet have "
            "a trustworthy ref. Exact matching is the default for actions; contains matching is "
            "for discovery, and ambiguous results are returned for narrowing instead of silently "
            "choosing the first element. This tool does not click or modify a match."
        ),
        schema=object_schema(props),
    )
