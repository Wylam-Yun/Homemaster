"""Definition for browser_history."""

from homemaster.tools.browser._common import ExecutionProof, object_schema, registered


def build(session: object):
    return registered(
        session=session,
        name="browser_history",
        operation="history",
        effects=("browser.navigate",),
        capabilities=("device.control",),
        proof=ExecutionProof.STRUCTURED_RECEIPT,
        description=(
            "Move the current tab through browser history with `back`, `forward`, or `reload`. Use "
            "this only for history traversal in the active tab; use `browser_navigate` for a new "
            "URL and `browser_tabs` for another tab. Success reports the action, previous and final "
            "URL, title, page generation, and whether a navigation actually occurred."
        ),
        schema=object_schema(
            {
                "action": {
                    "type": "string",
                    "enum": ["back", "forward", "reload"],
                    "description": "History operation.",
                },
                "wait_until": {
                    "type": "string",
                    "enum": ["domcontentloaded", "load", "networkidle"],
                    "description": "Load-state fence for the resulting history navigation.",
                },
                "timeout_ms": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Bounded navigation timeout.",
                },
            },
            ("action",),
        ),
    )
