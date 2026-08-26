"""Definition for browser_extract."""

from homemaster.tools.browser._common import (
    TARGET_SCHEMA,
    ExecutionProof,
    object_schema,
    registered,
)


def build(session: object):
    return registered(
        session=session,
        name="browser_extract",
        operation="extract",
        effects=("read",),
        capabilities=("device.read",),
        proof=ExecutionProof.NONE,
        description=(
            "Extract the readable content of the current page as cleaned Markdown and return a "
            "paragraph-aware bounded chunk. Use this for articles, documentation, reports, search "
            "results, or other long-form content that would be noisy or too large in a DOM snapshot. "
            "Continue a long page with the returned `next_start_char`; use `browser_read` for exact "
            "element properties and `browser_inspect` for actionable controls. This tool does not "
            "execute page actions or grant an action target."
        ),
        schema=object_schema(
            {
                "scope": TARGET_SCHEMA,
                "frame_ref": {"type": "string", "description": "Frame identity."},
                "chunk_size": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum Markdown chunk size.",
                },
                "start_char": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Cursor returned by the previous extraction.",
                },
            }
        ),
    )
