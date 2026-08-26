"""Definition for browser_inspect."""

from homemaster.tools.browser._common import (
    TARGET_SCHEMA,
    ExecutionProof,
    object_schema,
    registered,
)


def build(session: object):
    props = {
        "view": {
            "type": "string",
            "enum": ["dom", "ax", "hybrid", "frames"],
            "description": "Bounded page representation to return.",
        },
        "scope": TARGET_SCHEMA,
        "frame_ref": {
            "type": "string",
            "description": "Frame identity returned by a frames inspection.",
        },
        "role": {"type": "string", "description": "ARIA/native role filter."},
        "name": {"type": "string", "description": "Accessible-name filter."},
        "label": {"type": "string", "description": "Associated label filter."},
        "text": {"type": "string", "description": "Visible text filter."},
        "testid": {"type": "string", "description": "Allowlisted test id filter."},
        "match": {
            "type": "string",
            "enum": ["exact", "contains", "regex"],
            "description": "Exact, contains, or read-only regex matching.",
        },
        "interactive_only": {"type": "boolean", "description": "Return actionable controls only."},
        "actionable_only": {
            "type": "boolean",
            "description": "Exclude disabled or obscured controls.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 500,
            "description": "Maximum returned targets.",
        },
        "diff_from": {
            "type": "string",
            "description": "Earlier retained snapshot id for bounded diff metadata.",
        },
    }
    return registered(
        session=session,
        name="browser_inspect",
        operation="inspect",
        effects=("read",),
        capabilities=("device.read",),
        proof=ExecutionProof.NONE,
        description=(
            "Read a bounded DOM, Accessibility, or hybrid snapshot of the current page and assign "
            "stable target refs to actionable elements. Use this to understand an unfamiliar page, "
            "enumerate frames, tables, scroll containers, Shadow DOM, and compound controls, or "
            "compare a later snapshot with an earlier one. Use `browser_find` for a focused semantic "
            "query, `browser_read` for one exact property, `browser_extract` for long-form page "
            "content, and `browser_screenshot` for visual layout. This tool never changes the page."
        ),
        schema=object_schema(props),
    )
